// Copyright 2000-2025 JetBrains s.r.o. and contributors. Use of this source code is governed by the Apache 2.0 license.
package com.jetbrains.lang.dart.analyzer.lsp

import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.editor.Document
import com.intellij.openapi.editor.RangeMarker
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.jetbrains.lang.dart.analyzer.getDartFileInfo
import org.eclipse.lsp4j.CreateFile
import org.eclipse.lsp4j.DeleteFile
import org.eclipse.lsp4j.Position
import org.eclipse.lsp4j.RenameFile
import org.eclipse.lsp4j.TextEdit
import org.eclipse.lsp4j.WorkspaceEdit
import java.io.IOException
import kotlin.math.min

internal object LspWorkspaceEditApplier {
    /**
     * Applies a [WorkspaceEdit] to IntelliJ documents.
     * Returns an error message on failure, or null on success.
     *
     * Uses [RangeMarker]s so that edits within a file automatically adjust
     * as earlier edits shift offsets — no manual reverse-sorting needed.
     */
    fun applyWorkspaceEdit(project: Project, workspaceEdit: WorkspaceEdit, commandName: String): String? {
        var error: String? = null
        try {
            WriteCommandAction.runWriteCommandAction(project, commandName, null, {
                error = doApply(project, workspaceEdit)
            })
        } catch (e: Exception) {
            return e.message ?: "Unknown error applying workspace edit"
        }
        return error
    }

    private fun doApply(project: Project, workspaceEdit: WorkspaceEdit): String? {
        val documentChanges = workspaceEdit.documentChanges
        if (documentChanges != null) {
            for (change in documentChanges) {
                if (change.isLeft) {
                    val docEdit = change.left ?: continue
                    val uri = docEdit.textDocument?.uri ?: continue
                    val file = getDartFileInfo(project, uri).findFile() ?: continue
                    val err = applyTextEdits(file, docEdit.edits.orEmpty())
                    if (err != null) return err
                } else if (change.isRight) {
                    val err = applyResourceOperation(change.right)
                    if (err != null) return err
                }
            }
            return null
        }

        val changes = workspaceEdit.changes
        if (changes != null) {
            for ((uri, textEdits) in changes) {
                val file = getDartFileInfo(project, uri).findFile() ?: continue
                val err = applyTextEdits(file, textEdits.orEmpty())
                if (err != null) return err
            }
        }
        return null
    }

    private fun applyTextEdits(file: VirtualFile, edits: List<TextEdit>): String? {
        if (edits.isEmpty()) return null
        val document = FileDocumentManager.getInstance().getDocument(file)
            ?: return "Cannot get document for ${file.path}"

        // Create RangeMarkers for all edits before modifying the document.
        // RangeMarkers automatically adjust as earlier edits shift offsets.
        val markers = edits.mapNotNull { edit -> toRangeMarker(document, edit) }
        try {
            for ((marker, newText) in markers) {
                if (marker.isValid) {
                    document.replaceString(marker.startOffset, marker.endOffset, newText)
                }
            }
        } finally {
            markers.forEach { (marker, _) -> marker.dispose() }
        }
        return null
    }

    private data class MarkedEdit(val marker: RangeMarker, val newText: String)

    private fun toRangeMarker(document: Document, edit: TextEdit): MarkedEdit? {
        val startOffset = toOffset(document, edit.range.start) ?: return null
        val endOffset = toOffset(document, edit.range.end) ?: return null
        val marker = document.createRangeMarker(startOffset, endOffset)
        marker.isGreedyToLeft = true
        marker.isGreedyToRight = true
        val newText = edit.newText?.replace("\r", "") ?: ""
        return MarkedEdit(marker, newText)
    }

    private fun applyResourceOperation(operation: org.eclipse.lsp4j.ResourceOperation): String? {
        return try {
            when (operation) {
                is CreateFile -> {
                    val path = java.net.URI(operation.uri).path ?: return null
                    val file = java.io.File(path)
                    file.parentFile?.mkdirs()
                    file.createNewFile()
                    null
                }
                is DeleteFile -> {
                    findFileByUri(operation.uri)?.delete(this)
                    null
                }
                is RenameFile -> {
                    val file = findFileByUri(operation.oldUri) ?: return null
                    val newName = operation.newUri.substringAfterLast('/')
                    file.rename(this, newName)
                    null
                }
                else -> null
            }
        } catch (e: IOException) {
            e.message ?: "Resource operation failed"
        }
    }

    private fun findFileByUri(uri: String): VirtualFile? {
        // Use a lightweight resolution — getDartFileInfo requires a project for non-local files,
        // but resource operations typically target local files.
        return try {
            val path = java.net.URI(uri).path ?: return null
            com.intellij.openapi.vfs.LocalFileSystem.getInstance().findFileByPath(path)
        } catch (_: Exception) {
            null
        }
    }

    private fun toOffset(document: Document, position: Position?): Int? {
        if (position == null) return null
        val lineCount = document.lineCount
        val line = position.line
        if (line >= lineCount && position.character == 0) return document.textLength
        if (line < 0 || line >= lineCount) return null
        val lineStartOffset = document.getLineStartOffset(line)
        val lineEndOffset = document.getLineEndOffset(line)
        val character = min(position.character, lineEndOffset - lineStartOffset)
        return (lineStartOffset + character).takeIf { it <= document.textLength }
    }
}
