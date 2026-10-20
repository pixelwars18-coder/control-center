#!/usr/bin/env python3
"""Mod Manager: turn mods on and off per game, set load order and spot overlapping files."""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from core import store
from core.ui import T, Card, fmt_bytes, label, make_app


class ModTree(QTreeWidget):
    """Mod list where dragging a row emits the new order instead of nesting items."""
    reordered = Signal(list)

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["On", "Mod", "Version", "Type", "Size", "Updated", "Notes"])
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(False)
        h = self.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(6, QHeaderView.Interactive)
        for c in (0, 2, 3, 4, 5):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)

    def dropEvent(self, e):
        src = self.currentItem()
        target = self.itemAt(e.position().toPoint())
        e.ignore()
        if not src or target is src:
            return
        names = [self.topLevelItem(i).data(1, Qt.UserRole) for i in range(self.topLevelItemCount())]
        name = src.data(1, Qt.UserRole)
        names.remove(name)
        if target is None:
            names.append(name)
        else:
            idx = names.index(target.data(1, Qt.UserRole))
            below = e.position().y() > self.visualItemRect(target).center().y()
            names.insert(idx + below, name)
        self.reordered.emit(names)


class AddGameDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add a game")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.name = QLineEdit(placeholderText="e.g. Stardew Valley")
        self.path = QLineEdit(placeholderText="Folder the game's mod loader reads")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row = QHBoxLayout(); row.addWidget(self.path); row.addWidget(browse)
        form.addRow("Game name", self.name)
        form.addRow("Mods folder", row)
        hint = label("Each file or folder inside the mods folder counts as one mod. Turning a mod off moves it "
                     "into a hidden .disabled folder there; nothing is deleted.", "muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the mods folder", str(Path.home()))
        if d:
            self.path.setText(d)
            if not self.name.text():
                self.name.setText(Path(d).parent.name if Path(d).name.lower() in ("mods", "plugins") else Path(d).name)


class ModManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mod Manager")
        self.resize(1200, 760)
        self.games = []
        self.current = None
        self.loading = False

        split = QSplitter()
        split.setHandleWidth(16)
        split.setChildrenCollapsible(False)

        # Sidebar
        side = Card("Games")
        self.game_list = QListWidget()
        self.game_list.setStyleSheet("QListWidget { border: 0; }")
        self.game_list.currentItemChanged.connect(self.pick_game)
        side.lay.addWidget(self.game_list)
        add = QPushButton("Add game…"); add.setObjectName("primary")
        add.clicked.connect(self.add_game)
        side.lay.addWidget(add)
        side.setMinimumWidth(220)
        split.addWidget(side)

        # Main panel
        main = QWidget()
        mv = QVBoxLayout(main); mv.setContentsMargins(0, 0, 0, 0); mv.setSpacing(10)
        head = QHBoxLayout()
        self.title = label("Mod Manager", "title")
        self.count = label("", "muted")
        self.conflict_badge = label("")
        self.conflict_badge.setStyleSheet(f"color: {T['bad']}; font-weight: 600")
        open_btn = QPushButton("Open folder"); open_btn.clicked.connect(self.open_folder)
        self.remove_btn = QPushButton("Remove game"); self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self.remove_game)
        for w in (self.title, self.count, self.conflict_badge):
            head.addWidget(w)
        head.addStretch(); head.addWidget(open_btn); head.addWidget(self.remove_btn)
        mv.addLayout(head)
        self.path_lbl = label("", "muted")
        self.path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        mv.addWidget(self.path_lbl)

        tools = QHBoxLayout()
        self.filter = QLineEdit(placeholderText="Filter mods…")
        self.filter.textChanged.connect(self.render_mods)
        tools.addWidget(self.filter, 1)
        for text, fn in (("Enable all", lambda: self.set_all(True)), ("Disable all", lambda: self.set_all(False)),
                         ("Export load order…", self.export), ("Rescan", self.reload)):
            b = QPushButton(text); b.clicked.connect(fn); tools.addWidget(b)
        mv.addLayout(tools)

        self.tree = ModTree()
        self.tree.itemChanged.connect(self.item_changed)
        self.tree.currentItemChanged.connect(self.show_detail)
        self.tree.reordered.connect(self.reorder)
        mv.addWidget(self.tree, 3)

        lower = QHBoxLayout(); lower.setSpacing(12)
        self.detail = Card("Selected mod")
        f = QFormLayout()
        self.d_version, self.d_url = QLineEdit(), QLineEdit(placeholderText="Nexus, GitHub, Thunderstore…")
        self.d_notes = QPlainTextEdit(); self.d_notes.setFixedHeight(70)
        f.addRow("Version", self.d_version); f.addRow("Source", self.d_url); f.addRow("Notes", self.d_notes)
        self.detail.lay.addLayout(f)
        brow = QHBoxLayout()
        save = QPushButton("Save details"); save.setObjectName("primary"); save.clicked.connect(self.save_detail)
        visit = QPushButton("Open source link"); visit.clicked.connect(self.open_source)
        brow.addWidget(save); brow.addWidget(visit); brow.addStretch()
        self.detail.lay.addLayout(brow)
        self.detail.setEnabled(False)
        self.conf_card = Card("Overlapping files")
        hint = label("These files exist in more than one enabled mod. If the game merges mods into one folder, "
                     "the later mod in load order usually wins.", "muted")
        hint.setWordWrap(True)
        self.conf_card.lay.addWidget(hint)
        self.conf_list = QTreeWidget(); self.conf_list.setHeaderLabels(["File", "Mods"]); self.conf_list.setRootIsDecorated(False)
        self.conf_list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.conf_card.lay.addWidget(self.conf_list)
        lower.addWidget(self.detail, 1); lower.addWidget(self.conf_card, 1)
        mv.addLayout(lower, 2)

        self.empty = label("Add a game to get started. Point it at the folder its mod loader reads mods from, "
                           "such as BepInEx/plugins, a Minecraft mods folder or a game's Mods directory.", "muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        mv.addWidget(self.empty)
        split.addWidget(main)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 960])

        wrap = QWidget(); wl = QVBoxLayout(wrap); wl.setContentsMargins(20, 20, 20, 20); wl.addWidget(split)
        self.setCentralWidget(wrap)

        # Refresh when Jarvis (or anything else) changes the data or the mod folders.
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self.debounce.start())
        self.watcher.directoryChanged.connect(lambda _: self.debounce.start())
        self.debounce = QTimer(self, singleShot=True, interval=300, timeout=self.reload)
        self.reload()

    # ---------------------------------------------------------------- data
    def game(self):
        return next((g for g in self.games if g["id"] == self.current), None)

    def reload(self):
        self.games = store.list_games()
        if not self.game() and self.games:
            self.current = self.games[0]["id"]
        paths = [str(store.DATA)]
        if store.MODS_FILE.exists():
            paths.append(str(store.MODS_FILE))
        for g in self.games:
            for p in (Path(g["path"]), Path(g["path"]) / ".disabled"):
                if p.is_dir():
                    paths.append(str(p))
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        self.watcher.addPaths(paths)
        self.render()

    def act(self, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Mod Manager", str(e))
        self.reload()

    # ---------------------------------------------------------------- render
    def render(self):
        self.game_list.blockSignals(True)
        self.game_list.clear()
        for g in self.games:
            on = sum(m["enabled"] for m in g["mods"])
            it = QListWidgetItem(f"{g['name']}   ({on}/{len(g['mods'])})")
            it.setData(Qt.UserRole, g["id"])
            self.game_list.addItem(it)
            if g["id"] == self.current:
                self.game_list.setCurrentItem(it)
        self.game_list.blockSignals(False)

        g = self.game()
        has = g is not None
        for w in (self.tree, self.detail, self.conf_card, self.filter, self.remove_btn, self.path_lbl):
            w.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.title.setText("Mod Manager"); self.count.setText(""); self.conflict_badge.setText("")
            return
        self.title.setText(g["name"])
        self.path_lbl.setText(g.get("error") or g["path"])
        on = sum(m["enabled"] for m in g["mods"])
        self.count.setText(f"{on} of {len(g['mods'])} enabled")
        n = len(g["conflicts"])
        self.conflict_badge.setText(f"· {n} overlapping file{'s' if n != 1 else ''}" if n else "")
        self.conf_card.setVisible(bool(n))
        self.conf_list.clear()
        for c in g["conflicts"]:
            self.conf_list.addTopLevelItem(QTreeWidgetItem([c["file"], ", ".join(c["mods"])]))
        self.render_mods()

    def render_mods(self):
        g = self.game()
        if not g:
            return
        selected = self.tree.currentItem().data(1, Qt.UserRole) if self.tree.currentItem() else None
        q = self.filter.text().lower()
        self.loading = True
        self.tree.clear()
        for m in g["mods"]:
            if q and q not in m["name"].lower() and q not in m.get("notes", "").lower():
                continue
            name = m["name"] + ("   ⚠ overlap" if m["conflict"] else "")
            it = QTreeWidgetItem(["", name, m.get("version", ""), "folder" if m["isDir"] else "file", fmt_bytes(m["size"]),
                                  time.strftime("%Y-%m-%d", time.localtime(m["modified"])), m.get("notes", "").replace("\n", " ")])
            it.setData(1, Qt.UserRole, m["name"])
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            it.setCheckState(0, Qt.Checked if m["enabled"] else Qt.Unchecked)
            if not m["enabled"]:
                for c in range(1, 7):
                    it.setForeground(c, QColor(T["muted"]))
            if m["conflict"]:
                it.setForeground(1, QColor(T["bad"]))
            self.tree.addTopLevelItem(it)
            if m["name"] == selected:
                self.tree.setCurrentItem(it)
        self.tree.setDragEnabled(not q)
        self.loading = False
        self.show_detail(self.tree.currentItem(), None)

    def mod(self, name):
        return next((m for m in self.game()["mods"] if m["name"] == name), None)

    # ---------------------------------------------------------------- actions
    def pick_game(self, item, _):
        if item:
            self.current = item.data(Qt.UserRole)
            self.filter.clear()
            self.render()

    def add_game(self):
        dlg = AddGameDialog(self)
        if dlg.exec():
            try:
                self.current = store.add_game(dlg.name.text(), dlg.path.text())
            except ValueError as e:
                QMessageBox.warning(self, "Mod Manager", str(e))
            self.reload()

    def remove_game(self):
        g = self.game()
        if QMessageBox.question(self, "Remove game", f"Stop tracking {g['name']}? Your mod files are not touched.") == QMessageBox.Yes:
            self.current = None
            self.act(store.remove_game, g["id"])

    def item_changed(self, item, column):
        if self.loading or column != 0:
            return
        self.act(store.set_mod_enabled, self.current, item.data(1, Qt.UserRole), item.checkState(0) == Qt.Checked)

    def set_all(self, enable):
        g = self.game()
        if not g:
            return
        errors = []
        for m in g["mods"]:
            if m["enabled"] != enable:
                try:
                    store.set_mod_enabled(g["id"], m["name"], enable)
                except (ValueError, OSError) as e:
                    errors.append(str(e))
        if errors:
            QMessageBox.warning(self, "Mod Manager", "\n".join(errors))
        self.reload()

    def reorder(self, names):
        self.act(store.set_order, self.current, names)

    def show_detail(self, item, _):
        m = self.mod(item.data(1, Qt.UserRole)) if item and self.game() else None
        self.detail.setEnabled(bool(m))
        self.d_version.setText(m.get("version", "") if m else "")
        self.d_url.setText(m.get("url", "") if m else "")
        self.d_notes.setPlainText(m.get("notes", "") if m else "")

    def save_detail(self):
        item = self.tree.currentItem()
        if item:
            self.act(store.set_meta, self.current, item.data(1, Qt.UserRole), version=self.d_version.text(),
                     url=self.d_url.text(), notes=self.d_notes.toPlainText())

    def open_source(self):
        url = self.d_url.text().strip()
        if url.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))

    def open_folder(self):
        g = self.game()
        if g and Path(g["path"]).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(g["path"]))

    def export(self):
        g = self.game()
        if not g:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export load order", str(Path.home() / f"{g['id']}-load-order.txt"))
        if path:
            lines = [f"{i}. {m['name']}" + (f" v{m['version']}" if m.get("version") else "")
                     for i, m in enumerate((m for m in g["mods"] if m["enabled"]), 1)]
            Path(path).write_text(f"# {g['name']} load order\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    app = make_app("Mod Manager", "◆")
    win = ModManager()
    win.show()
    sys.exit(app.exec())
