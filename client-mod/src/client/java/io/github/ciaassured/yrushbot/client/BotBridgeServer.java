package io.github.ciaassured.yrushbot.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Objects;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

public final class BotBridgeServer implements AutoCloseable {
    private final BridgeConfig config;
    private final Queue<JsonObject> incomingMessages = new ConcurrentLinkedQueue<>();
    private final ExecutorService executor = Executors.newCachedThreadPool(runnable -> {
        Thread thread = new Thread(runnable, "yrush-bot-bridge");
        thread.setDaemon(true);
        return thread;
    });

    private volatile boolean running;
    private volatile ServerSocket serverSocket;
    private volatile ClientConnection connection;

    public BotBridgeServer(BridgeConfig config) {
        this.config = Objects.requireNonNull(config, "config");
    }

    public void start() {
        if (running) {
            return;
        }

        running = true;
        executor.execute(this::listen);
    }

    public void drainIncoming(Consumer<JsonObject> consumer) {
        JsonObject message;
        while ((message = incomingMessages.poll()) != null) {
            consumer.accept(message);
        }
    }

    public void send(JsonObject message) {
        ClientConnection activeConnection = connection;
        if (activeConnection != null) {
            activeConnection.send(message);
        }
    }

    private void listen() {
        try (ServerSocket socket = new ServerSocket(
            config.port(),
            config.backlog(),
            InetAddress.getByName(config.host())
        )) {
            serverSocket = socket;
            while (running) {
                Socket client = socket.accept();
                replaceConnection(new ClientConnection(client, incomingMessages));
            }
        } catch (IOException ex) {
            if (running) {
                YRushBotClient.LOGGER.error("YRush bot bridge stopped unexpectedly", ex);
            }
        } finally {
            serverSocket = null;
        }
    }

    private void replaceConnection(ClientConnection newConnection) {
        ClientConnection oldConnection = connection;
        if (oldConnection != null) {
            oldConnection.close();
        }
        connection = newConnection;
        newConnection.start(executor);
        newConnection.send(Protocol.hello());
        YRushBotClient.LOGGER.info("Python bridge client connected from {}", newConnection.remoteAddress());
    }

    @Override
    public void close() {
        running = false;

        ClientConnection activeConnection = connection;
        if (activeConnection != null) {
            activeConnection.close();
            connection = null;
        }

        ServerSocket activeSocket = serverSocket;
        if (activeSocket != null) {
            try {
                activeSocket.close();
            } catch (IOException ex) {
                YRushBotClient.LOGGER.warn("Failed to close bridge server socket", ex);
            }
        }

        executor.shutdownNow();
    }

    private static final class ClientConnection implements AutoCloseable {
        private final Socket socket;
        private final Queue<JsonObject> incomingMessages;
        private volatile BufferedWriter writer;
        private volatile boolean connected = true;

        private ClientConnection(Socket socket, Queue<JsonObject> incomingMessages) {
            this.socket = socket;
            this.incomingMessages = incomingMessages;
        }

        private String remoteAddress() {
            return socket.getRemoteSocketAddress().toString();
        }

        private void start(ExecutorService executor) {
            executor.execute(this::readLoop);
        }

        private void send(JsonObject message) {
            BufferedWriter activeWriter = writer;
            if (!connected || activeWriter == null) {
                return;
            }

            try {
                synchronized (this) {
                    activeWriter.write(message.toString());
                    activeWriter.newLine();
                    activeWriter.flush();
                }
            } catch (IOException ex) {
                YRushBotClient.LOGGER.warn("Failed to send bridge message", ex);
                close();
            }
        }

        private void readLoop() {
            try (
                Socket ignored = socket;
                BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                BufferedWriter output = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))
            ) {
                writer = output;
                String line;
                while (connected && (line = reader.readLine()) != null) {
                    try {
                        incomingMessages.add(JsonParser.parseString(line).getAsJsonObject());
                    } catch (RuntimeException ex) {
                        YRushBotClient.LOGGER.warn("Ignoring invalid bridge JSON: {}", line);
                    }
                }
            } catch (IOException ex) {
                if (connected) {
                    YRushBotClient.LOGGER.info("Python bridge client disconnected: {}", ex.getMessage());
                }
            } finally {
                connected = false;
                writer = null;
            }
        }

        @Override
        public void close() {
            connected = false;
            try {
                socket.close();
            } catch (IOException ex) {
                YRushBotClient.LOGGER.warn("Failed to close bridge client socket", ex);
            }
        }
    }
}
