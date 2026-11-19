import threading
import unittest

from core.compute_fabric import (
    CPUBackend,
    ComputeBackendError,
    ComputeCapabilities,
    ComputeCapacityError,
    ComputeFabricError,
    ComputeRequest,
    ComputeScheduler,
    ModelLifecycleManager,
)


class ComputeFabricTests(unittest.TestCase):
    def setUp(self):
        self.backend = CPUBackend(memory_capacity_bytes=100, max_concurrency=2)
        self.scheduler = ComputeScheduler((self.backend,))

    def test_cpu_backend_executes_operation(self):
        self.assertEqual(self.scheduler.execute(ComputeRequest(), lambda: 42), 42)

    def test_memory_budget_is_enforced_and_released(self):
        with self.scheduler.reserve(ComputeRequest(memory_bytes=80)):
            with self.assertRaises(ComputeCapacityError):
                with self.scheduler.reserve(ComputeRequest(memory_bytes=21)):
                    self.fail("over-budget reservation should not be admitted")
        self.assertEqual(
            self.scheduler.stats()["cpu"]["reserved_memory_bytes"], 0
        )

    def test_concurrency_limit_is_enforced(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def operation():
            entered.set()
            release.wait(timeout=2)

        def run_worker():
            try:
                self.scheduler.execute(ComputeRequest(), operation)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run_worker)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.scheduler.reserve(ComputeRequest()):
                with self.assertRaises(ComputeCapacityError):
                    with self.scheduler.reserve(ComputeRequest()):
                        pass
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_operation_failure_releases_reservation(self):
        def fail():
            raise RuntimeError("expected")

        with self.assertRaisesRegex(RuntimeError, "expected"):
            self.scheduler.execute(ComputeRequest(memory_bytes=30), fail)
        self.assertEqual(
            self.scheduler.stats()["cpu"],
            {"reserved_memory_bytes": 0, "resident_memory_bytes": 0, "transient_memory_bytes": 0, "resident_models": 0, "active_operations": 0},
        )

    def test_rejects_invalid_resource_values(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ComputeRequest(memory_bytes=value)
        with self.assertRaises(ValueError):
            ComputeCapabilities("cpu", "gpu", True, 10, 1)

    def test_unknown_or_unavailable_backend_is_not_executed(self):
        with self.assertRaises(LookupError):
            self.scheduler.execute(ComputeRequest(backend="cuda"), lambda: None)

        class UnavailableBackend(CPUBackend):
            @property
            def capabilities(self):
                return ComputeCapabilities("offline", "cpu", False, 10, 1)

        scheduler = ComputeScheduler((UnavailableBackend(),))
        with self.assertRaises(ComputeFabricError):
            scheduler.execute(ComputeRequest(backend="offline"), lambda: None)

    def test_cuda_backend_uses_torch_device_context_and_reports_vram_snapshot(self):
        from contextlib import nullcontext
        from core.compute_fabric import CUDABackend

        class FakeCuda:
            def is_available(self): return True
            def device_count(self): return 1
            def get_device_name(self, index): return "NVIDIA Test GPU"
            def mem_get_info(self, index): return (4096, 8192)
            def device(self, index): return nullcontext()

        class FakeTorch:
            cuda = FakeCuda()

        backend = CUDABackend(torch_module=FakeTorch())
        self.assertTrue(backend.capabilities.available)
        self.assertEqual(backend.capabilities.memory_capacity_bytes, 4096)
        self.assertEqual(backend.capabilities.device, "cuda")
        self.assertEqual(backend.execute(lambda: "cuda-result"), "cuda-result")

    def test_cuda_backend_fails_closed_without_runtime(self):
        from core.compute_fabric import CUDABackend, ComputeBackendError

        class FakeCuda:
            def is_available(self): return False
            def device_count(self): return 0

        class FakeTorch:
            cuda = FakeCuda()

        backend = CUDABackend(torch_module=FakeTorch())
        self.assertFalse(backend.capabilities.available)
        with self.assertRaises(ComputeBackendError):
            backend.execute(lambda: None)

    def test_google_tpu_backend_uses_jax_default_device_and_operator_budget(self):
        from contextlib import nullcontext
        from core.compute_fabric import GoogleTPUBackend

        class Device:
            platform = "tpu"

        class FakeJax:
            def devices(self, backend=None):
                return [Device()] if backend == "tpu" else []
            def default_device(self, device):
                return nullcontext()

        backend = GoogleTPUBackend(
            memory_capacity_bytes=123456, jax_module=FakeJax(),
        )
        self.assertTrue(backend.capabilities.available)
        self.assertEqual(backend.capabilities.memory_capacity_bytes, 123456)
        self.assertEqual(backend.execute(lambda: "tpu-result"), "tpu-result")

    def test_google_tpu_backend_unavailable_without_tpu_device(self):
        from core.compute_fabric import GoogleTPUBackend, ComputeBackendError

        class FakeJax:
            def devices(self, backend=None): return []

        backend = GoogleTPUBackend(memory_capacity_bytes=0, jax_module=FakeJax())
        self.assertFalse(backend.capabilities.available)
        with self.assertRaises(ComputeBackendError):
            backend.execute(lambda: None)


    def test_accelerator_backends_are_scheduler_admitted_and_release_resources(self):
        from contextlib import nullcontext
        from core.compute_fabric import CUDABackend, GoogleTPUBackend

        class FakeCuda:
            def is_available(self): return True
            def device_count(self): return 1
            def get_device_name(self, index): return "NVIDIA Test GPU"
            def mem_get_info(self, index): return (4096, 8192)
            def device(self, index): return nullcontext()

        class FakeTorch:
            cuda = FakeCuda()

        class Device:
            platform = "tpu"

        class FakeJax:
            def devices(self, backend=None):
                return [Device()] if backend == "tpu" else []
            def default_device(self, device): return nullcontext()

        backends = (
            CUDABackend(torch_module=FakeTorch()),
            GoogleTPUBackend(memory_capacity_bytes=8192, jax_module=FakeJax()),
        )
        scheduler = ComputeScheduler(backends)
        for backend, budget in (("cuda", 1024), ("tpu", 2048)):
            with self.subTest(backend=backend):
                result = scheduler.execute(
                    ComputeRequest(backend=backend, memory_bytes=budget),
                    lambda: f"{backend}-scheduled",
                )
                self.assertEqual(result, f"{backend}-scheduled")
                self.assertEqual(
                    scheduler.stats()[backend],
                    {"reserved_memory_bytes": 0, "resident_memory_bytes": 0, "transient_memory_bytes": 0, "resident_models": 0, "active_operations": 0},
                )

    def test_accelerator_execution_failure_releases_scheduler_reservation(self):
        from contextlib import nullcontext
        from core.compute_fabric import CUDABackend, ComputeBackendError

        class FakeCuda:
            def is_available(self): return True
            def device_count(self): return 1
            def get_device_name(self, index): return "NVIDIA Test GPU"
            def mem_get_info(self, index): return (4096, 8192)
            def device(self, index): return nullcontext()

        class FakeTorch:
            cuda = FakeCuda()

        scheduler = ComputeScheduler((CUDABackend(torch_module=FakeTorch()),))

        def fail():
            raise RuntimeError("device operation failed")

        with self.assertRaises(ComputeBackendError):
            scheduler.execute(
                ComputeRequest(backend="cuda", memory_bytes=512), fail,
            )
        self.assertEqual(
            scheduler.stats()["cuda"],
            {"reserved_memory_bytes": 0, "resident_memory_bytes": 0, "transient_memory_bytes": 0, "resident_models": 0, "active_operations": 0},
        )


    def test_model_residency_persists_across_transient_inference_reservations(self):
        lease = self.scheduler.reserve_model(
            ComputeRequest(memory_bytes=70), model_id="model-a",
        )
        try:
            self.assertEqual(lease.state, "active")
            with self.scheduler.reserve(ComputeRequest(memory_bytes=30)):
                stats = self.scheduler.stats()["cpu"]
                self.assertEqual(stats["resident_memory_bytes"], 70)
                self.assertEqual(stats["transient_memory_bytes"], 30)
                self.assertEqual(stats["reserved_memory_bytes"], 100)
            stats = self.scheduler.stats()["cpu"]
            self.assertEqual(stats["resident_memory_bytes"], 70)
            self.assertEqual(stats["transient_memory_bytes"], 0)
            with self.assertRaises(ComputeCapacityError):
                self.scheduler.reserve_model(
                    ComputeRequest(memory_bytes=31), model_id="model-b",
                )
        finally:
            lease.release()
        self.assertEqual(lease.state, "released")
        lease.release()
        self.assertEqual(self.scheduler.stats()["cpu"]["reserved_memory_bytes"], 0)

    def test_concurrent_model_residency_admission_is_bounded(self):
        barrier = threading.Barrier(3)
        admitted = []
        failures = []

        def acquire(model_id):
            barrier.wait()
            try:
                admitted.append(self.scheduler.reserve_model(
                    ComputeRequest(memory_bytes=60), model_id=model_id,
                ))
            except ComputeCapacityError as exc:
                failures.append(exc)

        workers = [threading.Thread(target=acquire, args=(f"m-{i}",)) for i in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=2)
        try:
            self.assertEqual(len(admitted), 1)
            self.assertEqual(len(failures), 1)
        finally:
            for lease in admitted:
                lease.release()

    def test_poisoned_residency_is_retained_until_explicit_reconciliation(self):
        lease = self.scheduler.reserve_model(
            ComputeRequest(memory_bytes=80), model_id="model-uncertain",
        )
        lease.mark_unload_failed()
        with self.assertRaises(ComputeFabricError):
            lease.release()
        self.assertEqual(lease.state, "poisoned")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 80)
        with self.assertRaises(ComputeCapacityError):
            with self.scheduler.reserve(ComputeRequest(memory_bytes=21)):
                pass
        self.scheduler.confirm_model_unloaded(lease)
        self.assertEqual(lease.state, "released")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 0)

    def test_residency_lease_rejects_foreign_scheduler_reconciliation(self):
        lease = self.scheduler.reserve_model(
            ComputeRequest(memory_bytes=10), model_id="model-a",
        )
        other = ComputeScheduler((CPUBackend(memory_capacity_bytes=100),))
        with self.assertRaises(TypeError):
            other.confirm_model_unloaded(lease)
        lease.release()

    def test_residency_load_failure_can_release_admission(self):
        lease = self.scheduler.reserve_model(
            ComputeRequest(memory_bytes=50), model_id="model-load-failure",
        )
        lease.release()
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 0)


    def test_model_lifecycle_load_infer_and_unload(self):
        from core.compute_fabric import ModelLifecycleManager

        manager = ModelLifecycleManager(self.scheduler)
        events = []
        session = manager.load(
            ComputeRequest(memory_bytes=60), model_id="model-lifecycle",
            loader=lambda backend: events.append("load") or {"weights": "opaque"},
        )
        self.assertEqual(session.state, "ready")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 60)
        result = manager.infer(
            session, ComputeRequest(memory_bytes=20),
            lambda handle: (events.append("infer"), handle["weights"])[1],
        )
        self.assertEqual(result, "opaque")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 60)
        self.assertEqual(self.scheduler.stats()["cpu"]["transient_memory_bytes"], 0)
        manager.unload(session, lambda backend, handle: events.append("unload"))
        self.assertEqual(session.state, "unloaded")
        self.assertEqual(events, ["load", "infer", "unload"])
        self.assertEqual(self.scheduler.stats()["cpu"]["reserved_memory_bytes"], 0)
        manager.unload(session, lambda backend, handle: self.fail("unload must be idempotent"))

    def test_backend_lookup_failure_releases_model_admission(self):
        scheduler = ComputeScheduler((CPUBackend(memory_capacity_bytes=100),))
        manager = ModelLifecycleManager(scheduler)
        request = ComputeRequest(memory_bytes=40)
        original_backend_for = scheduler.backend_for

        def unavailable_backend(_backend_name):
            raise ComputeBackendError("backend became unavailable")

        scheduler.backend_for = unavailable_backend
        with self.assertRaisesRegex(ComputeBackendError, "backend became unavailable"):
            manager.load(request, model_id="lookup-failure", loader=lambda _backend: object())
        scheduler.backend_for = original_backend_for
        self.assertEqual(scheduler.stats()["cpu"]["resident_memory_bytes"], 0)
        self.assertEqual(scheduler.stats()["cpu"]["resident_models"], 0)

    def test_model_load_failure_releases_only_after_cleanup_confirmation(self):
        from core.compute_fabric import ModelLifecycleManager

        manager = ModelLifecycleManager(self.scheduler)

        def fail_load(backend):
            raise RuntimeError("load failed")

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            manager.load(
                ComputeRequest(memory_bytes=40), model_id="failed-model",
                loader=fail_load,
                cleanup_failed_load=lambda backend, model_id: None,
            )
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 0)

    def test_model_load_failure_without_confirmed_cleanup_poison_reservation(self):
        from core.compute_fabric import ModelLifecycleManager

        manager = ModelLifecycleManager(self.scheduler)

        def fail_load(backend):
            raise RuntimeError("partial allocation uncertain")

        with self.assertRaisesRegex(RuntimeError, "partial allocation uncertain"):
            manager.load(
                ComputeRequest(memory_bytes=40), model_id="uncertain-model",
                loader=fail_load,
            )
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 40)

    def test_model_unload_failure_blocks_inference_until_reconciliation(self):
        from core.compute_fabric import ModelLifecycleManager, ModelSessionError

        manager = ModelLifecycleManager(self.scheduler)
        session = manager.load(
            ComputeRequest(memory_bytes=60), model_id="unload-failure",
            loader=lambda backend: object(),
        )

        def fail_unload(backend, handle):
            raise RuntimeError("unload uncertain")

        with self.assertRaisesRegex(RuntimeError, "unload uncertain"):
            manager.unload(session, fail_unload)
        self.assertEqual(session.state, "unhealthy")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 60)
        with self.assertRaises(ModelSessionError):
            manager.infer(session, ComputeRequest(), lambda handle: None)
        manager.reconcile_unloaded(session)
        self.assertEqual(session.state, "unloaded")
        self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 0)

    def test_model_lifecycle_rejects_foreign_session_and_backend_mismatch(self):
        from core.compute_fabric import ModelLifecycleManager, ModelSessionError

        manager = ModelLifecycleManager(self.scheduler)
        other = ModelLifecycleManager(self.scheduler)
        session = manager.load(
            ComputeRequest(memory_bytes=10), model_id="owned-model",
            loader=lambda backend: "handle",
        )
        try:
            with self.assertRaises(TypeError):
                other.infer(session, ComputeRequest(), lambda handle: handle)
            with self.assertRaises(ModelSessionError):
                manager.infer(
                    session, ComputeRequest(backend="cuda"),
                    lambda handle: handle,
                )
        finally:
            manager.unload(session, lambda backend, handle: None)

    def test_unload_waits_for_inflight_inference_before_releasing_residency(self):
        from core.compute_fabric import ModelLifecycleManager

        manager = ModelLifecycleManager(self.scheduler)
        session = manager.load(
            ComputeRequest(memory_bytes=60), model_id="concurrent-model",
            loader=lambda backend: "handle",
        )
        entered = threading.Event()
        release = threading.Event()
        events = []

        def infer_worker():
            manager.infer(
                session, ComputeRequest(memory_bytes=10),
                lambda handle: (entered.set(), release.wait(timeout=2), events.append("infer")),
            )

        worker = threading.Thread(target=infer_worker)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        unload_done = threading.Event()

        def unload_worker():
            manager.unload(session, lambda backend, handle: events.append("unload"))
            unload_done.set()

        unloader = threading.Thread(target=unload_worker)
        unloader.start()
        try:
            self.assertFalse(unload_done.wait(timeout=0.05))
            self.assertEqual(self.scheduler.stats()["cpu"]["resident_memory_bytes"], 60)
        finally:
            release.set()
            worker.join(timeout=2)
            unloader.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(unloader.is_alive())
        self.assertTrue(unload_done.is_set())
        self.assertEqual(events, ["infer", "unload"])
        self.assertEqual(self.scheduler.stats()["cpu"]["reserved_memory_bytes"], 0)

    def test_duplicate_backend_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.scheduler.register(CPUBackend())


if __name__ == "__main__":
    unittest.main()
