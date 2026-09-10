/*
 * Copyright 2026 The Chromium Authors. All rights reserved.
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 */

package com.jetbrains.dart.vmService

import com.google.gson.JsonObject
import com.jetbrains.lang.dart.ide.runner.server.vmService.vmServiceDrivers.service.VmService
import com.jetbrains.lang.dart.ide.runner.server.vmService.vmServiceDrivers.service.consumer.Consumer
import com.jetbrains.lang.dart.ide.runner.server.vmService.vmServiceDrivers.service.consumer.PerfettoTimelineConsumer
import com.jetbrains.lang.dart.ide.runner.server.vmService.vmServiceDrivers.service.element.PerfettoTimeline
import com.jetbrains.lang.dart.ide.runner.server.vmService.vmServiceDrivers.service.element.RPCError
import junit.framework.TestCase

class VmServiceDriverRequestTest : TestCase() {

  fun testPerfettoTimelineRequestSupportsLongTimestamps() {
    val service = RecordingVmService()
    val consumer = object : PerfettoTimelineConsumer {
      override fun received(response: PerfettoTimeline) = Unit
      override fun onError(error: RPCError) = Unit
    }

    service.getPerfettoVMTimeline(3_000_000_000L, 4_000_000_000L, consumer)

    assertEquals("getPerfettoVMTimeline", service.method)
    assertEquals(3_000_000_000L, service.params.get("timeOriginMicros").asLong)
    assertEquals(4_000_000_000L, service.params.get("timeExtentMicros").asLong)
    assertSame(consumer, service.consumer)
  }

  private class RecordingVmService : VmService() {
    lateinit var method: String
    lateinit var params: JsonObject
    lateinit var consumer: Consumer

    override fun request(method: String, params: JsonObject, consumer: Consumer) {
      this.method = method
      this.params = params
      this.consumer = consumer
    }
  }
}
