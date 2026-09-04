package gg.wellplayed.yrush.client.core;

import gg.wellplayed.yrush.protocol.v1.WireMessage;
import java.io.Closeable;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/** Single-peer TCP endpoint used by the Python trainer. */
public final class LoopbackServer implements Closeable {
  public interface Listener {
    void connected(long connectionId);

    void message(long connectionId, WireMessage message);

    void disconnected(long connectionId, Throwable cause);
  }

  private record Peer(long connectionId, Socket socket) {}

  private final int port;
  private final InetAddress bindAddress;
  private final Listener listener;
  private final AtomicBoolean running = new AtomicBoolean();
  private final AtomicLong nextConnectionId = new AtomicLong(1);
  private final Object outputLock = new Object();
  private ServerSocket serverSocket;
  private volatile Peer peer;

  public LoopbackServer(String bindAddress, int port, Listener listener) {
    if (port < 1 || port > 65535) {
      throw new IllegalArgumentException("port is outside 1..65535");
    }
    this.port = port;
    this.bindAddress = resolve(bindAddress);
    this.listener = Objects.requireNonNull(listener, "listener");
  }

  public void start() throws IOException {
    if (!running.compareAndSet(false, true)) {
      throw new IllegalStateException("trainer server already started");
    }
    serverSocket = new ServerSocket();
    serverSocket.setReuseAddress(true);
    serverSocket.bind(new InetSocketAddress(bindAddress, port), 1);
    Thread.ofPlatform().daemon(true).name("yrush-trainer-listener").start(this::acceptLoop);
  }

  public boolean connected() {
    Peer current = peer;
    return current != null && current.socket().isConnected() && !current.socket().isClosed();
  }

  public long connectionId() {
    Peer current = peer;
    return current == null || current.socket().isClosed() ? 0 : current.connectionId();
  }

  public void send(WireMessage message) throws IOException {
    send(connectionId(), message);
  }

  public void send(long connectionId, WireMessage message) throws IOException {
    synchronized (outputLock) {
      Peer current = peer;
      if (connectionId == 0
          || current == null
          || current.connectionId() != connectionId
          || current.socket().isClosed()) {
        throw new IOException("trainer is not connected");
      }
      FramedProtobuf.write(current.socket().getOutputStream(), message);
    }
  }

  private void acceptLoop() {
    while (running.get()) {
      Throwable failure = null;
      try {
        Socket socket = serverSocket.accept();
        socket.setTcpNoDelay(true);
        socket.setKeepAlive(true);
        long connectionId = nextConnectionId.getAndIncrement();
        peer = new Peer(connectionId, socket);
        listener.connected(connectionId);
        while (running.get() && !socket.isClosed()) {
          listener.message(connectionId, FramedProtobuf.read(socket.getInputStream()));
        }
      } catch (Throwable throwable) {
        failure = throwable;
      } finally {
        Peer current = peer;
        peer = null;
        if (current != null) {
          try {
            current.socket().close();
          } catch (IOException ignored) {
            // The original transport failure is more useful.
          }
          if (running.get()) {
            listener.disconnected(current.connectionId(), failure);
          }
        } else if (running.get() && !(failure instanceof SocketException)) {
          listener.disconnected(0, failure);
        }
      }
    }
  }

  @Override
  public void close() throws IOException {
    running.set(false);
    Peer current = peer;
    if (current != null) {
      current.socket().close();
    }
    if (serverSocket != null) {
      serverSocket.close();
    }
  }

  private static InetAddress resolve(String address) {
    if (address == null || address.isBlank()) {
      throw new IllegalArgumentException("bind address must not be blank");
    }
    try {
      return InetAddress.getByName(address);
    } catch (IOException exception) {
      throw new IllegalArgumentException("cannot resolve bind address: " + address, exception);
    }
  }
}
