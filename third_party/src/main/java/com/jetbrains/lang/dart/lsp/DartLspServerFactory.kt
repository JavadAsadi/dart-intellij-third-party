package com.jetbrains.lang.dart.lsp

import com.intellij.openapi.project.Project
import com.redhat.devtools.lsp4ij.LanguageServerFactory
import com.redhat.devtools.lsp4ij.client.features.LSPClientFeatures
import com.redhat.devtools.lsp4ij.server.StreamConnectionProvider

class DartLspServerFactory : LanguageServerFactory {
    override fun createConnectionProvider(project: Project): StreamConnectionProvider {
        return DartLspConnectionProvider(project)
    }

    override fun createClientFeatures(): LSPClientFeatures {
        return DartLspClientFeatures()
    }
}
