"""
Machine tab (expert view): motion controller parameters and status
Currently GRBL: settings ($$), offsets ($#), parser state ($G), build info ($I)
and the last status report (?)
Diagnostics: motion thread / controller communication counters
(cached, no controller traffic)
"""

import datetime
import time

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from uscope.gui.widgets import ArgusTab
from uscope.motion.grbl import (alarm_i2s, error_i2s, offset_k2s, parser_c2s,
                                status_k2s)


def format_coords(v, axes="XYZABC"):
    """
    "1.000,2.000,3.000" => "X 1.000  Y 2.000  Z 3.000"
    PRB style "1.000,2.000,3.000:1" keeps the suffix
    """
    if isinstance(v, dict):
        return "  ".join("%s %0.3f" % (k.upper(), v[k]) for k in sorted(v))
    v = str(v)
    coords, sep, suffix = v.partition(":")
    parts = coords.split(",")
    try:
        [float(x) for x in parts]
    except ValueError:
        return v
    if len(parts) == 1 or len(parts) > len(axes):
        return v
    ret = "  ".join("%s %s" % (axis, x) for axis, x in zip(axes, parts))
    return ret + sep + suffix


def format_duration(dt):
    dt = int(dt)
    if dt < 60:
        return "%u s" % dt
    if dt < 3600:
        return "%u m %u s" % (dt // 60, dt % 60)
    return "%u h %u m" % (dt // 3600, dt % 3600 // 60)


def format_age(t, now):
    if t is None:
        return "never"
    return "%s ago (%s)" % (format_duration(now - t),
                            datetime.datetime.fromtimestamp(t).strftime("%X"))


def last_event_rows(last, now, keys):
    """
    {"error": {"time": t, "code": 9, ...}} => ("Last error", "code=9 ...", age)
    """
    rows = []
    for k in keys:
        if k not in last:
            continue
        event = dict(last[k])
        t = event.pop("time")
        value = ", ".join("%s=%s" % kv for kv in event.items()) or "-"
        rows.append(("Last " + k.replace("_", " "), value,
                     format_age(t, now)))
    return rows


def parser_word_description(word):
    if word in parser_c2s:
        return parser_c2s[word]
    return parser_c2s.get(word[0], "")


class MachineTab(ArgusTab):
    # Display order
    SECTIONS = ("Status", "Offsets", "Diagnostics: motion thread",
                "Diagnostics: controller", "Diagnostics: errors / alarms",
                "Parser state ($G)", "Settings ($$)", "Build info ($I)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set from motion thread, consumed in _poll_misc
        self.refresh_result = None
        self.refresh_pending = False
        # Last rows displayed per section
        self.section_rows = {}
        self.shown_state_time = None

    def _initUI(self):
        layout = QVBoxLayout()

        row = QHBoxLayout()
        self.status_l = QLabel("")
        self.status_l.setWordWrap(True)
        row.addWidget(self.status_l, 1)
        self.refresh_pb = QPushButton("Refresh")
        self.refresh_pb.setToolTip(
            "Read settings, offsets and parser state from the controller. "
            "Only runs while the machine is idle")
        self.refresh_pb.clicked.connect(self.refresh)
        row.addWidget(self.refresh_pb)
        self.reset_counters_pb = QPushButton("Reset counters")
        self.reset_counters_pb.setToolTip(
            "Zero the diagnostics counters and forget the last events")
        self.reset_counters_pb.clicked.connect(
            lambda: self.ac.motion_thread.diagnostics_reset())
        row.addWidget(self.reset_counters_pb)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Name", "Value", "Description"))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.sections = {}
        for name in self.SECTIONS:
            item = QTreeWidgetItem([name])
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
            item.setFirstColumnSpanned(True)
            self.sections[name] = item
        layout.addWidget(self.tree, 1)

        self.setLayout(layout)

    def showEvent(self, event):
        super().showEvent(event)
        # First look: fetch values if we've never read them
        cache = self.ac.motion_thread.machine_state_cache()
        if cache is not None and cache["machine_state"] is None:
            self.refresh()

    def refresh(self):
        if self.refresh_pending:
            return
        if self.ac.motion_thread.machine_state_cache() is None:
            return
        if self.ac.is_planner_running():
            self.status_l.setText("Can't refresh while a scan is running")
            return

        def callback(_command, _args, ret):
            # Motion thread: hand off to the GUI thread (see _poll_misc)
            self.refresh_result = ret

        self.refresh_pending = True
        self.refresh_pb.setEnabled(False)
        self.status_l.setText("Refreshing…")
        self.ac.motion_thread.machine_state(callback=callback)

    def set_section(self, name, rows):
        """rows: list of (name, value, description)"""
        rows = [tuple(str(x) for x in row) for row in rows]
        old = self.section_rows.get(name)
        if old == rows:
            return
        self.section_rows[name] = rows
        section = self.sections[name]
        if old is not None and [r[0] for r in old] == [r[0] for r in rows]:
            # Same rows, new values: update in place (keeps selection)
            for i, row in enumerate(rows):
                item = section.child(i)
                for col, text in enumerate(row):
                    if item.text(col) != text:
                        item.setText(col, text)
                item.setToolTip(2, row[2])
            return
        section.takeChildren()
        for row in rows:
            item = QTreeWidgetItem(list(row))
            item.setToolTip(2, row[2])
            section.addChild(item)

    def status_rows(self, cache):
        qstatus = cache["qstatus"]
        if not qstatus:
            return [("", "No status report yet", "")]
        # Some fields are only sent every few reports: show the last seen
        fields = dict(cache["qstatus_sticky"])
        fields.update(qstatus)
        rows = [("State", fields.pop("status"), "Machine state")]
        mpos = fields.get("MPos")
        wco = fields.get("WCO")
        if isinstance(mpos, dict) and wco and "WPos" not in fields:
            try:
                offsets = [float(x) for x in wco.split(",")]
                fields["WPos"] = dict(
                    (k, mpos[k] - offset)
                    for k, offset in zip("xyz", offsets) if k in mpos)
            except ValueError:
                pass
        for k in status_k2s:
            if k in fields:
                rows.append((k, format_coords(fields.pop(k)), status_k2s[k]))
        for k, v in sorted(fields.items()):
            rows.append((k, v, ""))
        return rows

    def offset_rows(self, cache, state):
        rows = []
        wcs_offsets = cache.get("wcs_offsets")
        if wcs_offsets:
            rows.append(("pyuscope", format_coords(wcs_offsets),
                         "WCS offset applied by pyuscope (from G54)"))
        if state and "offsets" in state:
            for k, v in state["offsets"]:
                rows.append((k, format_coords(v), offset_k2s.get(k, "")))
        return rows

    def update_from_cache(self, cache):
        state = cache["machine_state"]
        self.set_section("Status", self.status_rows(cache))
        if state is not None and state["time"] == self.shown_state_time:
            return
        self.set_section("Offsets", self.offset_rows(cache, state))
        if state is None:
            return
        self.shown_state_time = state["time"]
        self.set_section("Parser state ($G)",
                         [(word, "", parser_word_description(word))
                          for word in state.get("parser", [])])
        self.set_section("Settings ($$)", state.get("settings", []))
        self.set_section("Build info ($I)",
                         [(k, v, "") for k, _sep, v in
                          (l.partition(":") for l in state.get("build", []))])

    def refresh_done(self, ret):
        self.refresh_pending = False
        self.refresh_pb.setEnabled(True)
        if isinstance(ret, BaseException):
            self.status_l.setText("Refresh failed: %s" % (ret, ))
            return
        errors = ret["errors"]
        if errors:
            self.status_l.setText("Refresh: " + "; ".join(
                "%s: %s" % (k, v) for k, v in errors.items()))
        else:
            self.status_l.setText(
                "Refreshed %s" %
                datetime.datetime.fromtimestamp(ret["time"]).strftime("%X"))

    def _poll_misc(self):
        if self.refresh_result is not None:
            ret = self.refresh_result
            self.refresh_result = None
            self.refresh_done(ret)
        if not self.isVisible():
            return
        self.update_diagnostics(self.ac.motion_thread.diagnostics())
        cache = self.ac.motion_thread.machine_state_cache()
        if cache is None:
            self.status_l.setText("Not available for this motion controller")
            self.refresh_pb.setEnabled(False)
            return
        self.update_from_cache(cache)

    """
    Diagnostics
    """

    def update_diagnostics(self, diag):
        now = time.time()
        self.set_section("Diagnostics: motion thread",
                         self.thread_diag_rows(diag, now))
        controller = diag["controller"]
        if controller is None:
            na = [("", "n/a", "Not available for this motion controller")]
            self.set_section("Diagnostics: controller", na)
            self.set_section("Diagnostics: errors / alarms", na)
            return
        self.set_section("Diagnostics: controller",
                         self.controller_diag_rows(controller, now))
        self.set_section("Diagnostics: errors / alarms",
                         self.code_diag_rows(controller, now))

    def thread_diag_rows(self, diag, now):
        c = diag["counters"]
        last = diag["last"]
        rows = [
            ("Running for", format_duration(now - diag["tstart"]), ""),
            ("Counters since", format_age(diag["treset"], now),
             "Reset counters"),
            ("Queue depth", diag["queue_depth"], "Commands waiting"),
            ("Idle", "yes" if diag["idle"] else "no", ""),
            ("Dead", diag["dead"] or "no", "Motion controller failed"),
            ("Processed", c.get("processed", 0), "Commands completed"),
        ]
        cmd = last.get("command")
        if cmd:
            rows.append(("Last command", "%s (%0.0f ms)" %
                         (cmd["command"], cmd["duration"] * 1000),
                         format_age(cmd["time"], now)))
        rows += [
            ("Longest command", "%u ms" % c.get("max_command_ms", 0),
             "Includes long moves / homing"),
            ("Exceptions", c.get("exceptions", 0), "Command raised"),
            ("Critical errors", c.get("critical_errors", 0),
             "Motion controller declared dead"),
            ("Axis exceeded", c.get("axis_exceeded", 0), "Soft limit hits"),
            ("Stops", c.get("stops", 0), ""),
            ("E-stops", c.get("estops", 0), ""),
            ("Jogs dropped (queue full)", c.get("jogs_dropped_queue_full",
                                                0), ""),
            ("Jogs dropped (disabled)", c.get("jogs_dropped_disabled",
                                              0), "Ex: during a scan"),
            ("Reconnects", c.get("reconnects", 0), ""),
        ]
        rows += last_event_rows(last, now,
                                ("exception", "critical_error",
                                 "axis_exceeded"))
        return rows

    def controller_diag_rows(self, diag, now):
        c = diag["counters"]
        dt = max(now - diag["treset"], 1e-3)
        rows = [
            ("Connected for", format_duration(now - diag["tconnect"]), ""),
            ("Counters since", format_age(diag["treset"], now),
             "Reset counters"),
            ("Last good response", format_age(diag["tlast_rx"], now), ""),
            ("Status polls", "%u (%0.1f / s)" %
             (c.get("status_polls", 0), c.get("status_polls", 0) / dt), "?"),
        ]
        for kind in ("command", "status"):
            l = diag["latencies"].get(kind)
            if l:
                value = "last %0.1f, avg %0.1f, max %0.1f ms" % (
                    l[3] * 1000, l[1] / l[0] * 1000, l[2] * 1000)
            else:
                value = "n/a"
            rows.append(("Latency (%s)" % kind, value, "Round trip"))
        for k, label, description in (
            ("commands", "Commands sent", "Lines expecting ok"),
            ("oks", "ok responses", ""),
            ("errors", "error:N responses", "See errors / alarms"),
            ("alarms", "ALARM:N messages", "See errors / alarms"),
            ("alarm_state_entries", "Alarm state entries",
             "Status changed to Alarm / Door"),
            ("timeouts", "Timeouts", "No response in time"),
            ("status_failures", "Status failures",
             "Bad / missing status report"),
            ("status_retries", "Status retries", ""),
            ("garbled_status_lines", "Garbled status lines",
             "Expected <...>"),
            ("unexpected_lines", "Unexpected lines", "Expected [...] or ok"),
            ("flushes", "Flushes", "Input / output discarded"),
            ("flushed_bytes", "Flushed bytes", "Unread data discarded"),
            ("recovers", "Recover attempts", "Resync after comms error"),
            ("recover_failures", "Recover failures", "Gave up => critical"),
            ("resets", "Resets", "^X sent"),
            ("reset_recovers", "Reset recovers", "Waited for reset banner"),
            ("move_retries", "Move retries", ""),
            ("jog_cancel_retries", "Jog cancel retries", ""),
            ("jog_cancel_bytes", "Jog cancels sent", "0x85"),
            ("dropped_jogs", "Dropped jogs", "Timed out, not retried"),
            ("motion_stalls", "Motion stalls", "No progress while moving"),
            ("homing_failures", "Homing failures", ""),
            ("rejected_queries", "Rejected queries",
             "Refresh while not idle"),
        ):
            rows.append((label, c.get(k, 0), description))
        rows += last_event_rows(
            diag["last"], now,
            ("error", "alarm", "alarm_state", "timeout", "status_failure",
             "garbled_status_line", "unexpected_line", "recover",
             "recover_failure", "flush_discard", "reset", "move_failure",
             "jog_cancel_failure", "motion_stall", "homing_failure",
             "rejected_query"))
        return rows

    def code_diag_rows(self, diag, now):
        rows = []
        for code, n in sorted(diag["error_codes"].items(),
                              key=lambda kv: (kv[0] is None, kv[0] or 0)):
            rows.append(("error:%s" % code, n,
                         error_i2s.get(code, "Unknown")))
        for code, n in sorted(diag["alarm_codes"].items(),
                              key=lambda kv: (kv[0] is None, kv[0] or 0)):
            rows.append(("ALARM:%s" % code, n,
                         alarm_i2s.get(code, "Unknown")))
        if not rows:
            rows.append(("", "None", ""))
        return rows
