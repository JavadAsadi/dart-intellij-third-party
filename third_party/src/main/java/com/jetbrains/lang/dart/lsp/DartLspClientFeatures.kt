package com.jetbrains.lang.dart.lsp

import com.intellij.openapi.vfs.VirtualFile
import com.redhat.devtools.lsp4ij.LSPIJUtils
import com.redhat.devtools.lsp4ij.client.features.LSPClientFeatures
import java.net.URI

class DartLspClientFeatures : LSPClientFeatures() {
    override fun isEnabled(file: VirtualFile): Boolean {
        return DartLspUtil.isLspMode() && super.isEnabled(file)
    }

    override fun getFileUri(file: VirtualFile): URI {
        return LSPIJUtils.toUri(file)
    }

    override fun findFileByUri(fileUri: String): VirtualFile? {
        return LSPIJUtils.findResourceFor(fileUri)
    }
}
