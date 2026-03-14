// Copyright 2000-2026 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
package com.jetbrains.dart.analysisServer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.openapi.editor.Document;
import com.intellij.openapi.vfs.VfsUtilCore;
import com.intellij.testFramework.fixtures.CodeInsightFixtureTestCase;
import com.intellij.testFramework.fixtures.impl.CodeInsightTestFixtureImpl;
import com.jetbrains.lang.dart.analyzer.DartAnalysisServerService;
import com.jetbrains.lang.dart.util.DartTestUtils;
import com.google.dart.server.ResponseListener;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public class DartLspHandleMethodSupportTest extends CodeInsightFixtureTestCase {
  @Override
  public void setUp() throws Exception {
    super.setUp();
    DartTestUtils.configureDartSdk(myModule, myFixture.getTestRootDisposable(), true);
    DartAnalysisServerService.getInstance(getProject()).serverReadyForRequest();
    ((CodeInsightTestFixtureImpl)myFixture).canChangeDocumentDuringHighlighting(true);
  }

  public void testHoverIsAcceptedThroughLspHandle() throws Exception {
    myFixture.configureByText("test.dart", """
      void main() {
        pri<caret>nt('x');
      }
      """);
    myFixture.doHighlighting();

    JsonObject response = sendLspHandleRequest("textDocument/hover", createTextDocumentPositionParams());
    System.out.println("hover lsp.handle response = " + response);

    assertTrue(response.toString(), response.has("result"));
    JsonObject result = response.getAsJsonObject("result");
    assertNotNull(result);
    assertTrue(response.toString(), result.has("lspResponse"));
    JsonObject lspResponse = result.getAsJsonObject("lspResponse");
    assertNotNull(lspResponse);
    assertTrue(response.toString(), lspResponse.has("result"));
    assertFalse(response.toString(), lspResponse.has("error"));
  }

  public void testPrepareRenameIsRejectedThroughLspHandle() throws Exception {
    myFixture.configureByText("test.dart", """
      void main() {
        var cou<caret>nt = 1;
        print(count);
      }
      """);
    myFixture.doHighlighting();

    JsonObject response = sendLspHandleRequest("textDocument/prepareRename", createTextDocumentPositionParams());
    System.out.println("prepareRename lsp.handle response = " + response);

    String responseText = response.toString();
    assertTrue(responseText, response.has("result"));
    JsonObject result = response.getAsJsonObject("result");
    assertNotNull(result);
    assertTrue(responseText, result.has("lspResponse"));
    JsonObject lspResponse = result.getAsJsonObject("lspResponse");
    assertNotNull(lspResponse);
    assertTrue(responseText, lspResponse.has("error"));
    JsonObject error = lspResponse.getAsJsonObject("error");
    assertNotNull(error);
    String message = error.get("message").getAsString();
    assertTrue(message, message.toLowerCase().contains("unknown method"));
    assertTrue(message, message.contains("textDocument/prepareRename"));
  }

  private JsonObject sendLspHandleRequest(String lspMethod, JsonObject lspParams) throws Exception {
    DartAnalysisServerService service = DartAnalysisServerService.getInstance(getProject());
    String requestId = service.generateUniqueId();
    assertNotNull(requestId);

    JsonObject lspMessage = new JsonObject();
    lspMessage.addProperty("id", requestId + "-inner");
    lspMessage.addProperty("jsonrpc", "2.0");
    lspMessage.addProperty("method", lspMethod);
    lspMessage.add("params", lspParams);

    JsonObject params = new JsonObject();
    params.add("lspMessage", lspMessage);

    JsonObject request = new JsonObject();
    request.addProperty("id", requestId);
    request.addProperty("method", "lsp.handle");
    request.add("params", params);

    CountDownLatch latch = new CountDownLatch(1);
    AtomicReference<String> rawResponse = new AtomicReference<>();
    ResponseListener listener = json -> {
      try {
        JsonObject response = JsonParser.parseString(json).getAsJsonObject();
        if (response.has("id") && requestId.equals(response.get("id").getAsString())) {
          rawResponse.compareAndSet(null, json);
          latch.countDown();
        }
      } catch (Exception ignored) {
      }
    };

    service.addResponseListener(listener);
    try {
      service.sendRequest(requestId, request);
      assertTrue("Timed out waiting for lsp.handle response to " + lspMethod, latch.await(20, TimeUnit.SECONDS));
    } finally {
      service.removeResponseListener(listener);
    }

    String json = rawResponse.get();
    assertNotNull("No raw response captured for " + lspMethod, json);
    return JsonParser.parseString(json).getAsJsonObject();
  }

  private JsonObject createTextDocumentPositionParams() {
    String fileUri = VfsUtilCore.virtualToIoFile(getFile().getVirtualFile()).toURI().toString();
    int offset = getEditor().getCaretModel().getOffset();
    Document document = getEditor().getDocument();
    int line = document.getLineNumber(offset);
    int character = offset - document.getLineStartOffset(line);

    JsonObject textDocument = new JsonObject();
    textDocument.addProperty("uri", fileUri);

    JsonObject position = new JsonObject();
    position.addProperty("line", line);
    position.addProperty("character", character);

    JsonObject params = new JsonObject();
    params.add("textDocument", textDocument);
    params.add("position", position);
    return params;
  }
}
