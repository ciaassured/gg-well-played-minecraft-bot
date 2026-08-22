package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.Closeable;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;

/** Single-peer, loopback-only TCP endpoint used by the local Python trainer. */
public final class LoopbackServer implements Closeable {
  public interface Listener {
    void connected();

    void message(WireMessage message);

    void disconnected(Throwable cause);
  }

  private final int port;
  private final Listener listener;
  private final AtomicBoolean running = new AtomicBoolean();
  private final Object outputLock = new Object();
  private ServerSocket serverSocket;
  private volatile Socket peer;

  public LoopbackServer(int port, Listener listener) {
    if (port < 1 || port > 65535) {
      throw new IllegalArgumentException("port is outside 1..65535");
    }
    this.port = port;
    this.listener = Objects.requireNonNull(listener, "listener");
  }

  public void start() throws IOException {
    if (!running.compareAndSet(false, true)) {
      throw new IllegalStateException("loopback server already started");
    }
    serverSocket = new ServerSocket();
    serverSocket.setReuseAddress(true);
    serverSocket.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 1);
    Thread.ofPlatform().daemon(true).name("jump-trainer-loopback").start(this::acceptLoop);
  }

  public boolean connected() {
    Socket socket = peer;
    return socket != null && socket.isConnected() && !socket.isClosed();
  }

  public void send(WireMessage message) throws IOException {
    synchronized (outputLock) {
      Socket socket = peer;
      if (socket == null || socket.isClosed()) {
        throw new IOException("trainer is not connected");
      }
      FramedProtobuf.write(socket.getOutputStream(), message);
    }
  }

  private void acceptLoop() {
    while (running.get()) {
      Throwable failure = null;
      try {
        Socket socket = serverSocket.accept();
        socket.setTcpNoDelay(true);
        socket.setKeepAlive(true);
        peer = socket;
        listener.connected();
        while (running.get() && !socket.isClosed()) {
          listener.message(FramedProtobuf.read(socket.getInputStream()));
        }
      } catch (Throwable throwable) {
        failure = throwable;
      } finally {
        Socket socket = peer;
        peer = null;
        if (socket != null) {
          try {
            socket.close();
          } catch (IOException ignored) {
            // The original transport failure is more useful.
          }
          if (running.get()) {
            listener.disconnected(failure);
          }
        } else if (running.get() && !(failure instanceof SocketException)) {
          listener.disconnected(failure);
        }
      }
    }
  }

  @Override
  public void close() throws IOException {
    running.set(false);
    Socket socket = peer;
    if (socket != null) {
      socket.close();
    }
    if (serverSocket != null) {
      serverSocket.close();
    }
  }
}
