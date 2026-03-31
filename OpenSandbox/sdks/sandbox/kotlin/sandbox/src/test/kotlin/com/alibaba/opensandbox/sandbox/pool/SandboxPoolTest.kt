/*
 * Copyright 2025 Alibaba Group Holding Ltd.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.alibaba.opensandbox.sandbox.pool

import com.alibaba.opensandbox.sandbox.config.ConnectionConfig
import com.alibaba.opensandbox.sandbox.domain.exceptions.PoolAcquireFailedException
import com.alibaba.opensandbox.sandbox.domain.exceptions.PoolEmptyException
import com.alibaba.opensandbox.sandbox.domain.exceptions.PoolNotRunningException
import com.alibaba.opensandbox.sandbox.domain.pool.AcquirePolicy
import com.alibaba.opensandbox.sandbox.domain.pool.PoolCreationSpec
import com.alibaba.opensandbox.sandbox.domain.pool.PoolState
import com.alibaba.opensandbox.sandbox.infrastructure.pool.InMemoryPoolStateStore
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.runs
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Duration
import java.util.concurrent.ExecutorService
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

class SandboxPoolTest {
    @Test
    fun `snapshot before start returns STOPPED and zero idle`() {
        val pool = buildPool()
        val snap = pool.snapshot()
        assertEquals(PoolState.STOPPED, snap.state)
        assertEquals(0, snap.idleCount)
    }

    @Test
    fun `start then snapshot returns RUNNING`() {
        val pool = buildPool()
        pool.start()
        try {
            val snap = pool.snapshot()
            assertEquals(PoolState.HEALTHY, snap.state)
        } finally {
            pool.shutdown(graceful = false)
        }
    }

    @Test
    fun `resize updates maxIdle`() {
        val pool = buildPool()
        pool.start()
        try {
            pool.resize(10)
            val snap = pool.snapshot()
            assertEquals(PoolState.HEALTHY, snap.state)
        } finally {
            pool.shutdown(graceful = false)
        }
    }

    @Test
    fun `shutdown graceful then snapshot returns STOPPED`() {
        val pool = buildPool()
        pool.start()
        pool.shutdown(graceful = true)
        val snap = pool.snapshot()
        assertEquals(PoolState.STOPPED, snap.state)
    }

    @Test
    fun `shutdown non-graceful then snapshot returns STOPPED`() {
        val pool = buildPool()
        pool.start()
        pool.shutdown(graceful = false)
        val snap = pool.snapshot()
        assertEquals(PoolState.STOPPED, snap.state)
    }

    @Test
    fun `acquire with FAIL_FAST and empty idle throws PoolEmptyException`() {
        val pool = buildPool()
        pool.start()
        try {
            assertThrows(PoolEmptyException::class.java) {
                pool.acquire(policy = AcquirePolicy.FAIL_FAST)
            }
        } finally {
            pool.shutdown(graceful = false)
        }
    }

    @Test
    fun `acquire with FAIL_FAST and stale idle throws PoolAcquireFailedException`() {
        val store = InMemoryPoolStateStore()
        val pool =
            SandboxPool.builder()
                .poolName("test-pool")
                .ownerId("test-owner")
                .maxIdle(2)
                .stateStore(store)
                .connectionConfig(ConnectionConfig.builder().build())
                .creationSpec(PoolCreationSpec.builder().image("ubuntu:22.04").build())
                .drainTimeout(Duration.ofMillis(50))
                .reconcileInterval(Duration.ofSeconds(30))
                .build()
        store.putIdle("test-pool", "non-existent-id")

        pool.start()
        try {
            assertThrows(PoolAcquireFailedException::class.java) {
                pool.acquire(policy = AcquirePolicy.FAIL_FAST)
            }
        } finally {
            pool.shutdown(graceful = false)
        }
    }

    @Test
    fun `acquire when pool is stopped throws PoolNotRunningException`() {
        val pool = buildPool()
        assertThrows(PoolNotRunningException::class.java) {
            pool.acquire(policy = AcquirePolicy.DIRECT_CREATE)
        }
    }

    @Test
    fun `releaseAllIdle drains store and returns count`() {
        val store = InMemoryPoolStateStore()
        val pool =
            SandboxPool.builder()
                .poolName("test-pool")
                .ownerId("test-owner")
                .maxIdle(2)
                .stateStore(store)
                .connectionConfig(ConnectionConfig.builder().build())
                .creationSpec(PoolCreationSpec.builder().image("ubuntu:22.04").build())
                .drainTimeout(Duration.ofMillis(50))
                .reconcileInterval(Duration.ofSeconds(30))
                .build()
        store.putIdle("test-pool", "id-1")
        store.putIdle("test-pool", "id-2")
        assertEquals(2, store.snapshotCounters("test-pool").idleCount)
        val released = pool.releaseAllIdle()
        assertEquals(2, released)
        assertEquals(0, store.snapshotCounters("test-pool").idleCount)
    }

    @Test
    fun `shutdown non-graceful force stops executors when await timeout`() {
        val pool = buildPool()
        val reconcileTask = mockk<ScheduledFuture<*>>()
        val scheduler = mockk<ScheduledExecutorService>()
        val warmup = mockk<ExecutorService>()

        every { reconcileTask.cancel(true) } returns true

        every { scheduler.shutdown() } just runs
        every { scheduler.awaitTermination(5, TimeUnit.SECONDS) } returnsMany listOf(false, true)
        every { scheduler.shutdownNow() } returns emptyList()

        every { warmup.shutdown() } just runs
        every { warmup.awaitTermination(5, TimeUnit.SECONDS) } returnsMany listOf(false, true)
        every { warmup.shutdownNow() } returns emptyList()

        setPrivateField(pool, "reconcileTask", reconcileTask)
        setPrivateField(pool, "scheduler", scheduler)
        setPrivateField(pool, "warmupExecutor", warmup)

        pool.shutdown(graceful = false)

        verify(exactly = 1) { reconcileTask.cancel(true) }
        verify(exactly = 1) { scheduler.shutdown() }
        verify(exactly = 1) { scheduler.shutdownNow() }
        verify(exactly = 2) { scheduler.awaitTermination(5, TimeUnit.SECONDS) }
        verify(exactly = 1) { warmup.shutdown() }
        verify(exactly = 1) { warmup.shutdownNow() }
        verify(exactly = 2) { warmup.awaitTermination(5, TimeUnit.SECONDS) }
    }

    @Test
    fun `shutdown non-graceful does not force stop executors when await succeeds`() {
        val pool = buildPool()
        val reconcileTask = mockk<ScheduledFuture<*>>()
        val scheduler = mockk<ScheduledExecutorService>()
        val warmup = mockk<ExecutorService>()

        every { reconcileTask.cancel(true) } returns true
        every { scheduler.shutdown() } just runs
        every { scheduler.awaitTermination(5, TimeUnit.SECONDS) } returns true
        every { scheduler.shutdownNow() } returns emptyList()
        every { warmup.shutdown() } just runs
        every { warmup.awaitTermination(5, TimeUnit.SECONDS) } returns true
        every { warmup.shutdownNow() } returns emptyList()

        setPrivateField(pool, "reconcileTask", reconcileTask)
        setPrivateField(pool, "scheduler", scheduler)
        setPrivateField(pool, "warmupExecutor", warmup)

        pool.shutdown(graceful = false)

        verify(exactly = 0) { scheduler.shutdownNow() }
        verify(exactly = 0) { warmup.shutdownNow() }
    }

    private fun buildPool(): SandboxPool {
        val config = ConnectionConfig.builder().build()
        val spec = PoolCreationSpec.builder().image("ubuntu:22.04").build()
        return SandboxPool.builder()
            .poolName("test-pool")
            .ownerId("test-owner")
            .maxIdle(2)
            .stateStore(InMemoryPoolStateStore())
            .connectionConfig(config)
            .creationSpec(spec)
            .drainTimeout(Duration.ofMillis(50))
            .reconcileInterval(Duration.ofSeconds(30))
            .build()
    }

    private fun setPrivateField(
        target: Any,
        fieldName: String,
        value: Any?,
    ) {
        val field = target.javaClass.getDeclaredField(fieldName)
        field.isAccessible = true
        field.set(target, value)
    }
}
