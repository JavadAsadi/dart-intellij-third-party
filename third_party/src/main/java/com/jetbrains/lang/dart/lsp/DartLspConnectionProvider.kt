package com.jetbrains.lang.dart.lsp

import com.intellij.openapi.project.Project
import com.jetbrains.lang.dart.sdk.DartSdk
import com.jetbrains.lang.dart.sdk.DartSdkUtil
import com.redhat.devtools.lsp4ij.server.ProcessStreamConnectionProvider

class DartLspConnectionProvider(project: Project) : ProcessStreamConnectionProvider() {
    init {
        val sdk = DartSdk.getDartSdk(project)
        if (sdk != null) {
            val dartExePath = DartSdkUtil.getDartExePath(sdk)
            commands = listOf(dartExePath, "language-server")
            workingDirectory = project.basePath
        }
    }
}
