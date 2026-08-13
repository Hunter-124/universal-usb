from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.media import (
    BLOCK_SIZE,
    MAX_DETACH_DEADLINE_SECONDS,
    UINT32_MAX,
    MediaBoundsError,
    MediaDetachTimeout,
    MediaIOError,
    MediaLockError,
    MediaReadOnlyError,
    MediaRemovalPreventedError,
    MediaState,
    MediaStateError,
    MediaValidationError,
    RawImage,
)


class RawImageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)

    def make_image(self, name: str = "disk.img", blocks: int = 2) -> Path:
        path = self.directory / name
        path.write_bytes(bytes((index % 251 for index in range(blocks * BLOCK_SIZE))))
        return path


class AttachmentValidationTests(RawImageTestCase):
    def test_attach_publishes_only_after_success_and_advances_epoch(self) -> None:
        path = self.make_image()
        image = RawImage()

        self.assertEqual(image.state, MediaState.CLOSED)
        self.assertEqual(image.epoch, 0)
        image.attach(path)
        self.assertEqual(image.state, MediaState.ATTACHED)
        self.assertEqual(image.path, os.fspath(path))
        self.assertEqual(image.block_count, 2)
        self.assertFalse(image.read_write)
        self.assertEqual(image.epoch, 1)

        image.detach(0.1)
        self.assertEqual(image.state, MediaState.CLOSED)
        self.assertIsNone(image.path)
        self.assertEqual(image.block_count, 0)
        self.assertEqual(image.epoch, 2)

    def test_rejects_non_regular_empty_and_unaligned_images(self) -> None:
        empty = self.directory / "empty.img"
        empty.touch()
        unaligned = self.directory / "unaligned.img"
        unaligned.write_bytes(b"x" * (BLOCK_SIZE + 1))

        for path in (self.directory, empty, unaligned):
            with self.subTest(path=path):
                image = RawImage()
                with self.assertRaises(MediaValidationError):
                    image.attach(path)
                self.assertEqual(image.state, MediaState.CLOSED)
                self.assertIsNone(image.path)
                self.assertEqual(image.epoch, 0)

    def test_rejects_more_than_uint32_blocks_without_publishing(self) -> None:
        path = self.make_image(blocks=1)
        oversized = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_size=(UINT32_MAX + 1) * BLOCK_SIZE,
        )
        image = RawImage()

        with mock.patch("uusb.media.os.fstat", return_value=oversized):
            with self.assertRaises(MediaValidationError):
                image.attach(path)

        self.assertEqual(image.state, MediaState.CLOSED)
        self.assertEqual(image.epoch, 0)
        image.attach(path)
        self.assertEqual(image.block_count, 1)
        image.close()

    def test_exclusive_nonblocking_lock_rejects_second_owner(self) -> None:
        path = self.make_image()
        first = RawImage()
        second = RawImage()
        first.attach(path)
        self.addCleanup(first.close)

        real_close = os.close
        started = time.monotonic()
        with mock.patch("uusb.media.os.close", wraps=real_close) as close:
            with self.assertRaises(MediaLockError):
                second.attach(path)
            close.assert_called_once()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(second.state, MediaState.CLOSED)

        second_descriptor = os.open(path, os.O_RDONLY)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(second_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(second_descriptor)


class BlockOperationTests(RawImageTestCase):
    def test_read_only_is_default_and_attach_never_truncates(self) -> None:
        path = self.make_image()
        original = path.read_bytes()
        image = RawImage()
        image.attach(path)

        self.assertEqual(image.read_block(0), original[:BLOCK_SIZE])
        with self.assertRaises(MediaReadOnlyError):
            image.write_block(0, b"z" * BLOCK_SIZE)
        image.close()
        self.assertEqual(path.read_bytes(), original)

    def test_write_handles_partial_progress_and_fsyncs_before_return(self) -> None:
        path = self.make_image()
        replacement = b"w" * BLOCK_SIZE
        image = RawImage()
        image.attach(path, read_write=True)
        real_pwrite = os.pwrite
        real_fsync = os.fsync
        calls: list[str] = []

        def partial_pwrite(fd: int, data: memoryview, offset: int) -> int:
            calls.append("write")
            return real_pwrite(fd, data[:73], offset)

        def tracking_fsync(fd: int) -> None:
            calls.append("fsync")
            real_fsync(fd)

        with (
            mock.patch("uusb.media.os.pwrite", side_effect=partial_pwrite),
            mock.patch("uusb.media.os.fsync", side_effect=tracking_fsync),
        ):
            image.write_block(1, replacement)

        self.assertGreater(calls.count("write"), 1)
        self.assertEqual(calls[-1], "fsync")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            self.assertEqual(os.pread(descriptor, BLOCK_SIZE, BLOCK_SIZE), replacement)
        finally:
            os.close(descriptor)
        image.close()

    def test_synchronize_and_close_both_fsync(self) -> None:
        path = self.make_image()
        image = RawImage()
        image.attach(path, read_write=True)
        real_fsync = os.fsync

        with mock.patch("uusb.media.os.fsync", wraps=real_fsync) as fsync:
            image.synchronize()
            self.assertEqual(fsync.call_count, 1)
            image.close()
            self.assertEqual(fsync.call_count, 2)
        self.assertEqual(image.epoch, 2)

    def test_block_address_and_payload_bounds(self) -> None:
        path = self.make_image(blocks=1)
        image = RawImage()
        image.attach(path, read_write=True)
        self.addCleanup(image.close)

        for lba in (-1, 1, True, 0.5):
            with self.subTest(lba=lba):
                with self.assertRaises(MediaBoundsError):
                    image.read_block(lba)  # type: ignore[arg-type]
        for data in (b"", b"x" * (BLOCK_SIZE - 1), b"x" * (BLOCK_SIZE + 1)):
            with self.subTest(length=len(data)):
                with self.assertRaises(MediaBoundsError):
                    image.write_block(0, data)

    def test_short_or_failed_read_raises_instead_of_returning_a_block(self) -> None:
        path = self.make_image(blocks=1)
        image = RawImage()
        image.attach(path)
        self.addCleanup(image.close)

        with mock.patch("uusb.media.os.pread", return_value=b""):
            with self.assertRaises(MediaIOError):
                image.read_block(0)
        with mock.patch(
            "uusb.media.os.pread",
            side_effect=OSError("injected read failure"),
        ):
            with self.assertRaises(MediaIOError):
                image.read_block(0)

    def test_partial_reads_are_combined_into_one_exact_block(self) -> None:
        path = self.make_image(blocks=1)
        image = RawImage()
        image.attach(path)
        self.addCleanup(image.close)

        with mock.patch(
            "uusb.media.os.pread",
            side_effect=(b"a" * 200, b"b" * 312),
        ):
            result = image.read_block(0)
        self.assertEqual(result, b"a" * 200 + b"b" * 312)
        self.assertEqual(len(result), BLOCK_SIZE)


class DetachLifecycleTests(RawImageTestCase):
    def test_prevent_allow_blocks_detach_but_close_still_cleans_up(self) -> None:
        path = self.make_image()
        image = RawImage()
        image.attach(path)
        image.prevent_removal(True)

        with self.assertRaises(MediaRemovalPreventedError):
            image.detach(0.1)
        self.assertEqual(image.state, MediaState.ATTACHED)
        self.assertTrue(image.removal_prevented)
        image.prevent_removal(False)
        image.detach(0.1)
        self.assertEqual(image.state, MediaState.CLOSED)

        image.attach(path)
        image.prevent_removal(True)
        image.close()
        self.assertEqual(image.state, MediaState.CLOSED)

    def test_detach_rejects_unbounded_or_invalid_deadlines(self) -> None:
        path = self.make_image()
        image = RawImage()
        image.attach(path)
        self.addCleanup(image.close)

        for deadline in (-1, float("inf"), float("nan"), MAX_DETACH_DEADLINE_SECONDS + 1):
            with self.subTest(deadline=deadline):
                with self.assertRaises(ValueError):
                    image.detach(deadline)
        self.assertEqual(image.state, MediaState.ATTACHED)

    def test_detaching_stops_new_work_and_waits_for_active_read(self) -> None:
        path = self.make_image(blocks=1)
        image = RawImage()
        image.attach(path)
        real_pread = os.pread
        read_started = threading.Event()
        permit_read = threading.Event()
        failures: list[BaseException] = []

        def slow_pread(fd: int, size: int, offset: int) -> bytes:
            read_started.set()
            if not permit_read.wait(1.0):
                raise RuntimeError("test read was not released")
            return real_pread(fd, size, offset)

        def reader() -> None:
            try:
                image.read_block(0)
            except BaseException as error:
                failures.append(error)

        def detacher() -> None:
            try:
                image.detach(1.0)
            except BaseException as error:
                failures.append(error)

        with mock.patch("uusb.media.os.pread", side_effect=slow_pread):
            read_thread = threading.Thread(target=reader)
            read_thread.start()
            self.assertTrue(read_started.wait(0.5))
            detach_thread = threading.Thread(target=detacher)
            detach_thread.start()
            limit = time.monotonic() + 0.5
            while image.state is not MediaState.DETACHING and time.monotonic() < limit:
                time.sleep(0.001)
            self.assertEqual(image.state, MediaState.DETACHING)
            with self.assertRaises(MediaStateError):
                image.read_block(0)
            permit_read.set()
            read_thread.join(1.0)
            detach_thread.join(1.0)

        self.assertFalse(read_thread.is_alive())
        self.assertFalse(detach_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(image.state, MediaState.CLOSED)
        self.assertEqual(image.epoch, 2)

    def test_detach_timeout_is_bounded_and_attachment_remains_usable(self) -> None:
        path = self.make_image(blocks=1)
        image = RawImage()
        image.attach(path)
        real_pread = os.pread
        read_started = threading.Event()
        permit_read = threading.Event()
        failures: list[BaseException] = []

        def slow_pread(fd: int, size: int, offset: int) -> bytes:
            read_started.set()
            if not permit_read.wait(1.0):
                raise RuntimeError("test read was not released")
            return real_pread(fd, size, offset)

        def reader() -> None:
            try:
                image.read_block(0)
            except BaseException as error:
                failures.append(error)

        with mock.patch("uusb.media.os.pread", side_effect=slow_pread):
            read_thread = threading.Thread(target=reader)
            read_thread.start()
            self.assertTrue(read_started.wait(0.5))
            started = time.monotonic()
            with self.assertRaises(MediaDetachTimeout):
                image.detach(0.01)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.25)
            self.assertEqual(image.state, MediaState.ATTACHED)
            permit_read.set()
            read_thread.join(1.0)

        self.assertFalse(read_thread.is_alive())
        self.assertEqual(failures, [])
        image.detach(0.1)

    def test_context_manager_closes_and_closed_operations_are_typed(self) -> None:
        path = self.make_image()
        image = RawImage()
        with image:
            image.attach(path)
            self.assertEqual(image.state, MediaState.ATTACHED)
        self.assertEqual(image.state, MediaState.CLOSED)
        image.close()
        with self.assertRaises(MediaStateError):
            image.read_block(0)
        with self.assertRaises(MediaStateError):
            image.synchronize()


if __name__ == "__main__":
    unittest.main()
