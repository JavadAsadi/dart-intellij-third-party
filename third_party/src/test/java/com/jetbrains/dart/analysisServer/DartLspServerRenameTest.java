// Copyright 2000-2025 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
package com.jetbrains.dart.analysisServer;

import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.util.registry.Registry;
import com.intellij.testFramework.fixtures.CodeInsightFixtureTestCase;
import com.intellij.testFramework.fixtures.impl.CodeInsightTestFixtureImpl;
import com.jetbrains.lang.dart.analyzer.DartAnalysisServerService;
import com.jetbrains.lang.dart.analyzer.DartFileInfoKt;
import com.jetbrains.lang.dart.analyzer.lsp.LspDartAnalysisClient;
import com.jetbrains.lang.dart.analyzer.lsp.LspWorkspaceEditApplier;
import com.jetbrains.lang.dart.util.DartTestUtils;
import com.intellij.openapi.editor.Document;
import org.eclipse.lsp4j.WorkspaceEdit;
import org.jetbrains.annotations.NotNull;

public class DartLspServerRenameTest extends CodeInsightFixtureTestCase {
  @Override
  public void setUp() throws Exception {
    super.setUp();
    Registry.get("dart.use.lsp.client").setValue(true, myFixture.getTestRootDisposable());
    DartTestUtils.configureDartSdk(myModule, myFixture.getTestRootDisposable(), true);
    DartAnalysisServerService.getInstance(getProject()).serverReadyForRequest();
    myFixture.setTestDataPath(DartTestUtils.BASE_TEST_DATA_PATH + getBasePath());
    ((CodeInsightTestFixtureImpl)myFixture).canChangeDocumentDuringHighlighting(true);
  }

  @Override
  protected String getBasePath() {
    return "/analysisServer/refactoring/rename";
  }

  @SuppressWarnings("KotlinInternalInJava")
  private void doTest(@NotNull String newName) {
    myFixture.configureByFile(getTestName(false) + ".dart");
    myFixture.doHighlighting(); // warm up server

    DartAnalysisServerService.getInstance(getProject()).updateFilesContent();

    LspDartAnalysisClient lspClient = LspDartAnalysisClient.Companion.getInstance(getProject());
    assertNotNull("LSP client should be available", lspClient);

    int offset = getEditor().getCaretModel().getOffset();
    Document document = getEditor().getDocument();
    int line = document.getLineNumber(offset);
    int character = offset - document.getLineStartOffset(line);
    String fileUri = DartFileInfoKt.getDartFileUri(getFile().getVirtualFile());

    // Prepare rename
    String placeholder = lspClient.prepareRename(fileUri, line, character);
    assertNotNull("prepareRename should succeed", placeholder);

    // Execute rename
    WorkspaceEdit workspaceEdit = lspClient.rename(fileUri, line, character, newName);
    assertNotNull("rename should return a WorkspaceEdit", workspaceEdit);

    // Apply
    ApplicationManager.getApplication().invokeAndWait(() -> {
      String error = LspWorkspaceEditApplier.INSTANCE.applyWorkspaceEdit(getProject(), workspaceEdit, "Rename");
      assertNull("applyWorkspaceEdit should succeed, but got: " + error, error);
    });

    // Validate
    myFixture.checkResultByFile(getTestName(false) + ".after.dart");
  }

  @SuppressWarnings("KotlinInternalInJava")
  public void testPrepareRenameUnrenameable() {
    myFixture.configureByFile("CheckInitialConditionsCannotCreate.dart");
    myFixture.doHighlighting();

    DartAnalysisServerService.getInstance(getProject()).updateFilesContent();

    LspDartAnalysisClient lspClient = LspDartAnalysisClient.Companion.getInstance(getProject());
    assertNotNull("LSP client should be available", lspClient);

    int offset = getEditor().getCaretModel().getOffset();
    Document document = getEditor().getDocument();
    int line = document.getLineNumber(offset);
    int character = offset - document.getLineStartOffset(line);
    String fileUri = DartFileInfoKt.getDartFileUri(getFile().getVirtualFile());

    String placeholder = lspClient.prepareRename(fileUri, line, character);
    assertNull("prepareRename should return null for unrenameable elements", placeholder);
  }

  public void testClass() {
    doTest("NewName");
  }

  public void testMethod() {
    doTest("newName");
  }
}
