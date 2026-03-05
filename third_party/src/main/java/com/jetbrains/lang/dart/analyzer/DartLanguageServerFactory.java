package com.jetbrains.lang.dart.analyzer;

import com.jetbrains.lang.dart.logging.PluginLogger;
import com.intellij.openapi.diagnostic.Logger;
import com.intellij.openapi.project.Project;
import com.redhat.devtools.lsp4ij.LanguageServerFactory;
import com.redhat.devtools.lsp4ij.server.StreamConnectionProvider;
import com.redhat.devtools.lsp4ij.client.features.LSPClientFeatures;
import com.redhat.devtools.lsp4ij.client.features.LSPHoverFeature;
import com.redhat.devtools.lsp4ij.client.features.LSPCompletionFeature;
import com.redhat.devtools.lsp4ij.client.features.LSPDiagnosticFeature;
import com.redhat.devtools.lsp4ij.client.features.LSPFormattingFeature;
import com.intellij.psi.PsiFile;
import org.jetbrains.annotations.NotNull;

import java.io.InputStream;
import java.io.OutputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.dart.server.ResponseListener;

public class DartLanguageServerFactory implements LanguageServerFactory {

  private static final Logger LOG = PluginLogger.INSTANCE.createLogger(DartLanguageServerFactory.class);

  public DartLanguageServerFactory() {
  }

  @Override
  public @NotNull StreamConnectionProvider createConnectionProvider(@NotNull Project project) {
    return new StreamConnectionProvider() {
      private InputStream lspInputStream; // lsp4ij reads from here
      private OutputStream dartToLspStream; // we write Dart server responses here

      private InputStream lspToDartStream; // we read lsp4ij requests from here
      private OutputStream lspOutputStream; // lsp4ij writes to here

      private QueuePipe dartToLspPipe;
      private QueuePipe lspToDartPipe;

      private Thread lspToDartThread;
      private ResponseListener dartResponseListener;
      private DartAnalysisServerService dasService;

      @Override
      public void start() {
        dartToLspPipe = new QueuePipe();
        lspInputStream = dartToLspPipe.getInputStream();
        dartToLspStream = dartToLspPipe.getOutputStream();

        lspToDartPipe = new QueuePipe();
        lspToDartStream = lspToDartPipe.getInputStream();
        lspOutputStream = lspToDartPipe.getOutputStream();

        dasService = DartAnalysisServerService.getInstance(project);

        // 1. Listen for Dart server responses and unwraps them for lsp4ij
        dartResponseListener = new ResponseListener() {
          @Override
          public void onResponse(String jsonString) {
            try {
              JsonObject jsonObject = JsonParser.parseString(jsonString).getAsJsonObject();
              JsonObject lspPayload = null;

              // Check if it is a response to our lsp.handle request
              if (jsonObject.has("result") && jsonObject.get("result").isJsonObject()) {
                JsonObject result = jsonObject.getAsJsonObject("result");
                if (result.has("lspResponse")) {
                  lspPayload = result.getAsJsonObject("lspResponse");
                }
              }

              // Check if it is a notification or server-to-client request
              if (lspPayload == null && jsonObject.has("params") && jsonObject.get("params").isJsonObject()) {
                JsonObject params = jsonObject.getAsJsonObject("params");
                if (params.has("lspMessage")) {
                  lspPayload = params.getAsJsonObject("lspMessage");
                }
              }

              if (lspPayload != null) {
                String lspMessageString = lspPayload.toString();
                LOG.info("Dart server sent lsp payload: " + lspMessageString);
                // Wrap in LSP standard Content-Length headers
                String message = "Content-Length: " + lspMessageString.getBytes(StandardCharsets.UTF_8).length
                    + "\r\n\r\n" + lspMessageString;
                try {
                  dartToLspStream.write(message.getBytes(StandardCharsets.UTF_8));
                  dartToLspStream.flush();
                } catch (IOException e) {
                  LOG.warn("Failed to write to dartToLspStream", e);
                }
              }
            } catch (Exception e) {
              LOG.warn("Failed to parse lspToDartStream", e);
              // Ignore parse errors from non-json or unrelated traffic
            }
          }
        };
        dasService.addResponseListener(dartResponseListener);

        // 2. Read lsp4ij requests and forward only hovers as `lsp.handle`
        lspToDartThread = new Thread(() -> {
          StringBuilder buffer = new StringBuilder();
          try {
            int b;
            while ((b = lspToDartStream.read()) != -1) {
              buffer.append((char) b);
              // A very naive detection of the end of a JSON-RPC message.
              // A robust implementation should parse Content-Length properly.
              String content = buffer.toString();
              if (content.endsWith("}")) {
                int braceCount = 0;
                int startIdx = content.indexOf("{");
                if (startIdx != -1) {
                  for (int i = startIdx; i < content.length(); i++) {
                    if (content.charAt(i) == '{')
                      braceCount++;
                    else if (content.charAt(i) == '}')
                      braceCount--;
                  }
                  if (braceCount == 0 && content.substring(0, startIdx).contains("Content-Length")) {
                    String jsonPart = content.substring(startIdx);
                    try {
                      JsonObject jsonObject = JsonParser.parseString(jsonPart).getAsJsonObject();
                      String method = jsonObject.has("method") ? jsonObject.get("method").getAsString() : null;

                      // Fake an initialize response so lsp4ij knows hover is supported
                      if ("initialize".equals(method)) {
                        String id = jsonObject.get("id").getAsString();
                        String fakeResponse = "{\"jsonrpc\":\"2.0\",\"id\":" + id
                            + ",\"result\":{\"capabilities\":{\"hoverProvider\":true,\"completionProvider\":{\"resolveProvider\":false}}}}";
                        String message = "Content-Length: " + fakeResponse.getBytes(StandardCharsets.UTF_8).length
                            + "\r\n\r\n" + fakeResponse;
                        dartToLspStream.write(message.getBytes(StandardCharsets.UTF_8));
                        dartToLspStream.flush();
                        LOG.info("Sent fake initialize response to lsp4ij");
                      } else if ("textDocument/hover".equals(method)) {
                        LOG.info("Forwarding hover request to Dart server");
                        // Wrap the LSP request inside a legacy `lsp.handle` request
                        JsonObject legacyRequest = new JsonObject();
                        // Generate a unique ID for the legacy request wrapper
                        String legacyId = dasService.generateUniqueId();
                        legacyRequest.addProperty("id", legacyId);
                        legacyRequest.addProperty("method", "lsp.handle");

                        JsonObject params = new JsonObject();
                        params.add("lspMessage", jsonObject);
                        legacyRequest.add("params", params);

                        // Send down to the Dart server
                        dasService.sendLspMessage(legacyRequest);
                      } else {
                        LOG.info("Ignored lsp4ij request: " + method);
                      }
                    } catch (Exception e) {
                      LOG.warn("Failed to parse lsp4ij message: " + jsonPart, e);
                    }
                    buffer.setLength(0); // clear buffer
                  }
                }
              }
            }
          } catch (IOException e) {
            String msg = e.getMessage();
            if (msg != null
                && (msg.contains("Pipe broken") || msg.contains("Stream closed") || msg.contains("Pipe closed"))) {
              // Expected when stopping or closing the IDE, ignore it
            } else {
              LOG.warn("Error reading from lspToDartStream", e);
            }
          }
        }, "LspToDartForwarder");
        lspToDartThread.setDaemon(true);
        lspToDartThread.start();
      }

      @Override
      public InputStream getInputStream() {
        return lspInputStream;
      }

      @Override
      public OutputStream getOutputStream() {
        return lspOutputStream;
      }

      @Override
      public void stop() {
        if (dasService != null && dartResponseListener != null) {
          dasService.removeResponseListener(dartResponseListener);
        }
        if (lspToDartThread != null) {
          lspToDartThread.interrupt();
        }
        try {
          if (dartToLspStream != null)
            dartToLspStream.close();
          if (lspOutputStream != null)
            lspOutputStream.close();
        } catch (IOException e) {
          LOG.warn("Error closing pipeds streams", e);
        }
      }
    };
  }

  @Override
  public @NotNull LSPClientFeatures createClientFeatures() {
    LSPClientFeatures features = new LSPClientFeatures();

    // Enable hover explicitly so we can test lsp4ij
    features.setHoverFeature(new LSPHoverFeature() {
      @Override
      public boolean isEnabled(@NotNull PsiFile file) {
        return true;
      }
    });

    // Disable completion so it doesn't shadow DartServerCompletionContributor
    features.setCompletionFeature(new LSPCompletionFeature() {
      @Override
      public boolean isEnabled(@NotNull PsiFile file) {
        return false;
      }
    });

    // Disable diagnostics so they don't double-report with legacy Annotators
    features.setDiagnosticFeature(new LSPDiagnosticFeature() {
      @Override
      public boolean isEnabled(@NotNull PsiFile file) {
        return false;
      }
    });

    // Disable formatting as we use the legacy DartStyleAction
    features.setFormattingFeature(new LSPFormattingFeature() {
      @Override
      public boolean isEnabled(@NotNull PsiFile file) {
        return false;
      }
    });

    return features;
  }

  private static class QueuePipe {
    private final BlockingQueue<byte[]> queue = new LinkedBlockingQueue<>();
    private volatile boolean closed = false;

    public InputStream getInputStream() {
      return new InputStream() {
        private byte[] current = null;
        private int index = 0;

        @Override
        public int read() throws IOException {
          if (current == null || index >= current.length) {
            try {
              current = queue.take();
              index = 0;
              if (current.length == 0)
                return -1; // EOF marker
            } catch (InterruptedException e) {
              throw new IOException(e);
            }
          }
          return current[index++] & 0xFF;
        }

        @Override
        public int read(byte[] b, int off, int len) throws IOException {
          if (b == null) {
            throw new NullPointerException();
          } else if (off < 0 || len < 0 || len > b.length - off) {
            throw new IndexOutOfBoundsException();
          } else if (len == 0) {
            return 0;
          }

          if (current == null || index >= current.length) {
            try {
              current = queue.take();
              index = 0;
              if (current.length == 0)
                return -1; // EOF
            } catch (InterruptedException e) {
              throw new IOException(e);
            }
          }
          int available = current.length - index;
          int toCopy = Math.min(len, available);
          System.arraycopy(current, index, b, off, toCopy);
          index += toCopy;
          return toCopy;
        }
      };
    }

    public OutputStream getOutputStream() {
      return new OutputStream() {
        @Override
        public void write(int b) throws IOException {
          if (closed)
            throw new IOException("Stream closed");
          queue.add(new byte[] { (byte) b });
        }

        @Override
        public void write(byte[] b, int off, int len) throws IOException {
          if (closed)
            throw new IOException("Stream closed");
          byte[] copy = new byte[len];
          System.arraycopy(b, off, copy, 0, len);
          queue.add(copy);
        }

        @Override
        public void close() throws IOException {
          closed = true;
          queue.add(new byte[0]); // EOF marker
        }
      };
    }
  }
}
