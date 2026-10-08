"""One window at a time (gui/single.py): a second launch hands its files to the running copy and quits; the
first launch runs when nothing listens. A launch while the first is still starting waits for it (trying again ten
times a second, and giving up after WAIT_MS when nothing ever listens), files that come before the window is ready
are kept for it, main() quits cleanly after a hand-over, and launches close together
reach the window as one drop, held while a question is on screen (main._take_launches). Each test uses a socket
name of its own, never the real one, so a copy of the app open on the machine is left alone."""
import uuid

from minute_filler.gui.single import SingleInstance, hand_over, socket_name


def fresh_name() -> str:
    return f"YinItAgreementForm-test-{uuid.uuid4().hex}"


def wait_for(qt, got: list, timeout_ms: int = 3000) -> None:
    """Runs the event loop until the running copy has taken the message."""
    from PySide6.QtCore import QElapsedTimer
    clock = QElapsedTimer()
    clock.start()
    while not got and clock.elapsed() < timeout_ms:
        qt.QApplication.processEvents()


def test_the_first_launch_runs_when_nothing_listens(qt):
    one = SingleInstance(fresh_name())
    try:
        assert hand_over(one, ["C:/orders/roe.pdf"]) is False
        assert one.server is not None and one.server.isListening()
    finally:
        one.close()


def test_a_second_launch_hands_its_files_to_the_first(qt):
    name = fresh_name()
    one, two = SingleInstance(name), SingleInstance(name)
    got: list = []
    one.files_received.connect(got.append)
    try:
        assert one.claim() is True
        assert hand_over(two, ["C:/orders/roe.pdf", "C:/orders/poe.eml"]) is True
        wait_for(qt, got)
        assert got == [["C:/orders/roe.pdf", "C:/orders/poe.eml"]]
        assert two.server is None  # the second launch never listens
    finally:
        one.close()


def test_a_bare_second_launch_just_shows_the_window(qt):
    name = fresh_name()
    one, two = SingleInstance(name), SingleInstance(name)
    got: list = []
    one.files_received.connect(got.append)
    try:
        one.claim()
        assert hand_over(two, []) is True
        wait_for(qt, got)
        assert got == [[]]
    finally:
        one.close()


def test_the_window_closes_its_socket_so_the_next_launch_runs(qt):
    name = fresh_name()
    one = SingleInstance(name)
    one.claim()
    one.close()
    nxt = SingleInstance(name)
    try:
        assert hand_over(nxt, []) is False
    finally:
        nxt.close()


def test_the_socket_is_named_for_the_app_and_the_user(monkeypatch):
    monkeypatch.setattr("minute_filler.gui.single.getpass.getuser", lambda: "Pat Reporter")
    assert socket_name() == "YinItAgreementForm-Pat_Reporter"


def test_a_launch_while_the_first_is_still_starting_waits_for_it(qt):
    """The first copy holds the mutex but doesn't listen yet (it is building its window): a second launch is not a
    first too, and hands its files over once the first listens (before: it opened a second window)."""
    from PySide6.QtCore import QTimer
    name = fresh_name()
    one, two = SingleInstance(name), SingleInstance(name)
    got: list = []
    one.files_received.connect(got.append)
    try:
        assert one._take_mutex() is True
        assert SingleInstance(name).claim() is False  # (two launches at once: only one is first)
        QTimer.singleShot(700, one._listen)
        assert hand_over(two, ["C:/orders/roe.pdf"]) is True
        wait_for(qt, got)
        assert got == [["C:/orders/roe.pdf"]]
    finally:
        one.close()


def test_waiting_for_a_copy_that_never_listens_does_not_spin(qt, monkeypatch):
    """The mutex is taken but nothing listens (a copy shutting down, or one that couldn't listen): the launch tries
    again every tenth of a second, not thousands of times a second, and gives up after WAIT_MS."""
    import time
    import minute_filler.gui.single as single
    tries = []

    class Counting(single.QLocalSocket):
        def connectToServer(self, *a):
            tries.append(1)
            return super().connectToServer(*a)
    monkeypatch.setattr(single, "QLocalSocket", Counting)
    monkeypatch.setattr(single, "WAIT_MS", 600)
    name = fresh_name()
    one = SingleInstance(name)
    try:
        assert one._take_mutex() is True
        start = time.monotonic()
        assert hand_over(SingleInstance(name), []) is False
        assert time.monotonic() - start >= 0.55 and len(tries) <= 10
    finally:
        one.close()


def test_files_that_come_before_the_window_is_ready_are_kept(qt):
    """A launch answered while the window was still being built: its files go to the window once it takes them."""
    name = fresh_name()
    one, two = SingleInstance(name), SingleInstance(name)
    try:
        one.claim()
        assert hand_over(two, ["C:/orders/roe.pdf"]) is True
        qt.QApplication.processEvents()
        got: list = []
        one.deliver(got.append)
        assert got == [["C:/orders/roe.pdf"]]
    finally:
        one.close()


def test_a_hand_over_from_main_quits_cleanly(qt, monkeypatch, tmp_path):
    """main() with a copy running: it hands the file over and returns 0 (before: TypeError, after the hand-over)."""
    import sys
    from minute_filler import log
    from minute_filler.main import main
    name = fresh_name()
    monkeypatch.setattr("minute_filler.gui.single.socket_name", lambda app="YinItAgreementForm": name)
    order = tmp_path / "Roe v Poe.pdf"
    order.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(sys, "argv", ["YinItAgreementForm", str(order)])
    one = SingleInstance(name)
    got: list = []
    one.files_received.connect(got.append)
    try:
        one.claim()
        assert main() == 0
        wait_for(qt, got)
        assert got == [[str(order.resolve())]]
    finally:
        one.close()
        log.shutdown()


def test_launches_close_together_are_one_drop_and_wait_for_a_question(qt):
    """Two launches' files, a moment apart, reach the window as one drop (a job per case and date, not the second day
    in the first day's job); while a modal question is on screen they wait for it to be answered."""
    from PySide6.QtCore import QElapsedTimer, Qt
    from minute_filler.main import _take_launches

    class Window(qt.QWidget):
        def __init__(self):
            super().__init__()
            self.drops = []

        def add_files(self, paths):
            self.drops.append(paths)

    def run(ms):
        clock = QElapsedTimer()
        clock.start()
        while clock.elapsed() < ms:
            qt.QApplication.processEvents()

    name = fresh_name()
    one = SingleInstance(name)
    win = Window()
    question = qt.QDialog()
    try:
        one.claim()
        _take_launches(qt.QApplication.instance(), win, one, ["C:/orders/roe 9-14.pdf"])
        assert hand_over(SingleInstance(name), ["C:/orders/roe 9-21.pdf"]) is True
        run(800)
        assert win.drops == [["C:/orders/roe 9-14.pdf", "C:/orders/roe 9-21.pdf"]]
        question.setWindowModality(Qt.ApplicationModal)
        question.show()
        assert hand_over(SingleInstance(name), ["C:/orders/poe.eml"]) is True
        run(800)
        assert len(win.drops) == 1  # (waits while the question is open)
        question.close()
        run(800)
        assert win.drops[1:] == [["C:/orders/poe.eml"]]
    finally:
        question.close()
        one.close()
