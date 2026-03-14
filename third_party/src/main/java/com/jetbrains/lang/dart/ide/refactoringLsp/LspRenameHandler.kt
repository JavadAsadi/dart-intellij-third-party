package com.jetbrains.lang.dart.ide.refactoringLsp

import com.intellij.CommonBundle
import com.intellij.ide.TitledHandler
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.actionSystem.DataContext
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.util.registry.Registry
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiFile
import com.intellij.refactoring.rename.RenameHandler
import com.intellij.refactoring.util.CommonRefactoringUtil
import com.jetbrains.lang.dart.DartBundle
import com.jetbrains.lang.dart.DartLanguage
import com.jetbrains.lang.dart.analyzer.DartAnalysisServerService
import com.jetbrains.lang.dart.analyzer.getDartFileUri
import com.jetbrains.lang.dart.analyzer.lsp.LspDartAnalysisClient
import com.jetbrains.lang.dart.analyzer.lsp.LspWorkspaceEditApplier
import org.eclipse.lsp4j.WorkspaceEdit

internal class LspRenameHandler : RenameHandler, TitledHandler {
    override fun getActionTitle(): String = DartBundle.message("action.title.dart.rename.refactoring")

    override fun isAvailableOnDataContext(dataContext: DataContext): Boolean {
        if (!Registry.`is`("dart.use.lsp.client", false)) return false

        val editor = CommonDataKeys.EDITOR.getData(dataContext) ?: return false
        val file = CommonDataKeys.VIRTUAL_FILE.getData(dataContext)
        if (!DartAnalysisServerService.isLocalAnalyzableFile(file)) return false

        val psiElement = CommonDataKeys.PSI_ELEMENT.getData(dataContext)
        if (psiElement != null) {
            return psiElement.language == DartLanguage.INSTANCE && psiElement !is PsiFile
        }

        val psiFile = CommonDataKeys.PSI_FILE.getData(dataContext)
        val elementAtOffset = psiFile?.findElementAt(editor.caretModel.offset)
        return elementAtOffset != null && elementAtOffset.language == DartLanguage.INSTANCE
    }

    override fun invoke(project: Project, editor: Editor?, file: PsiFile?, dataContext: DataContext) {
        if (editor == null) return
        performRename(project, editor, dataContext)
    }

    override fun invoke(project: Project, elements: Array<out PsiElement>, dataContext: DataContext?) {
        // Dart file rename is not handled using server yet
    }

    private fun performRename(project: Project, editor: Editor, context: DataContext) {
        val virtualFile = CommonDataKeys.VIRTUAL_FILE.getData(context) ?: return

        val caret = CommonDataKeys.CARET.getData(context)
        val element = CommonDataKeys.PSI_ELEMENT.getData(context)
        val offset = caret?.offset ?: element?.textOffset ?: return

        val document = editor.document
        val line = document.getLineNumber(offset)
        val character = offset - document.getLineStartOffset(line)

        val client = LspDartAnalysisClient.getInstance(project)
        if (client == null || !client.isSocketOpen()) {
            CommonRefactoringUtil.showErrorHint(
                project, editor,
                DartBundle.message("analysis.server.not.running"),
                CommonBundle.getErrorTitle(), null
            )
            return
        }

        val fileUri = getDartFileUri(virtualFile)

        val placeholder = prepareRename(project, client, fileUri, line, character)
        if (placeholder == null) {
            CommonRefactoringUtil.showErrorHint(
                project, editor,
                DartBundle.message("error.hint.cannot.rename.this.element"),
                CommonBundle.getErrorTitle(), null
            )
            return
        }

        val newName = Messages.showInputDialog(
            project,
            DartBundle.message("dialog.message.enter.new.name"),
            DartBundle.message("action.title.dart.rename.refactoring"),
            Messages.getQuestionIcon(),
            placeholder,
            null,
        )
        if (newName == null || newName == placeholder) return

        val workspaceEdit = executeRename(project, client, fileUri, line, character, newName)
        if (workspaceEdit == null) {
            CommonRefactoringUtil.showErrorHint(
                project, editor,
                DartBundle.message("error.hint.rename.failed"),
                CommonBundle.getErrorTitle(), null
            )
            return
        }

        val error = LspWorkspaceEditApplier.applyWorkspaceEdit(
            project, workspaceEdit,
            DartBundle.message("action.title.dart.rename.refactoring"),
        )
        if (error != null) {
            CommonRefactoringUtil.showErrorHint(project, editor, error, CommonBundle.getErrorTitle(), null)
        }
    }

    private fun prepareRename(
        project: Project,
        client: LspDartAnalysisClient,
        fileUri: String,
        line: Int,
        character: Int,
    ): String? {
        var result: String? = null
        ProgressManager.getInstance().run(object : Task.Modal(
            project,
            DartBundle.message("progress.title.dart.preparing.rename"),
            true,
        ) {
            override fun run(indicator: ProgressIndicator) {
                result = client.prepareRename(fileUri, line, character)
            }
        })
        return result
    }

    private fun executeRename(
        project: Project,
        client: LspDartAnalysisClient,
        fileUri: String,
        line: Int,
        character: Int,
        newName: String,
    ): WorkspaceEdit? {
        var result: WorkspaceEdit? = null
        ProgressManager.getInstance().run(object : Task.Modal(
            project,
            DartBundle.message("progress.title.dart.rename"),
            true,
        ) {
            override fun run(indicator: ProgressIndicator) {
                result = client.rename(fileUri, line, character, newName)
            }
        })
        return result
    }
}