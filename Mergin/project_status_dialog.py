import os
from itertools import groupby

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QSizePolicy,
    QPushButton,
    QLabel,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QStandardItemModel, QStandardItem, QIcon

from qgis.gui import QgsGui
from qgis.core import Qgis, QgsApplication, QgsProject, QgsMapLayer, QgsMessageLog

from .diff_dialog import DiffViewerDialog
from .validation import MultipleLayersWarning, warning_display_string
from .utils import is_versioned_file, icon_path

ui_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ui", "ui_status_dialog.ui")


class ProjectStatusDialog(QDialog):

    icons = {
        "added": "plus.svg",
        "removed": "trash.svg",
        "updated": "pencil.svg",
        "renamed": "pencil.svg",
        "table": "table.svg",
    }

    def __init__(
        self,
        pull_changes,
        push_changes,
        push_changes_summary,
        has_write_permissions,
        validation_results,
        mergin_project=None,
        parent=None,
    ):
        QDialog.__init__(self, parent)
        self.ui = uic.loadUi(ui_file, self)

        QgsGui.instance().enableAutoGeometryRestore(self)

        self.btn_sync = QPushButton("Sync")
        self.btn_sync.setIcon(QIcon(icon_path("refresh.svg")))
        # add sync button with AcceptRole. If dialog accepted we will start
        # sync, otherwise just close status dialog
        self.ui.buttonBox.addButton(self.btn_sync, QDialogButtonBox.AcceptRole)

        self.btn_view_changes.setIcon(QIcon(icon_path("file-diff.svg")))
        self.btn_view_changes.clicked.connect(self.show_changes)

        self.validation_results = validation_results
        self.mp = mergin_project

        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Status"])
        self.treeStatus.setModel(self.model)

        self.check_any_changes(pull_changes, push_changes)
        self.add_content(pull_changes, "Server Changes", True)
        self.add_content(push_changes, "Local Changes", False, push_changes_summary)
        self.treeStatus.expandAll()
        self.changes_summary = push_changes_summary

        if not self.validation_results:
            self.ui.lblWarnings.hide()
            self.ui.txtWarnings.hide()
            self.btn_sync.setStyleSheet("background-color: #90ee90")
        else:
            self.show_validation_results()
            self.btn_sync.setStyleSheet("background-color: #ffc800")

        has_files_to_replace = any(
            ["diff" not in file and is_versioned_file(file["path"]) for file in push_changes["updated"]]
        )
        info_text = self._get_info_text(has_files_to_replace, has_write_permissions, self.mp.has_unfinished_pull())
        for msg in info_text:
            lbl = QLabel(msg)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl.setWordWrap(True)
            self.ui.messageBar.pushWidget(lbl, Qgis.Warning)

    def _get_info_text(self, has_files_to_replace, has_write_permissions, has_unfinished_pull):
        msg = []
        if not has_write_permissions:
            msg.append(f"You don't have writing permissions to this project. Changes won't be synced!")

        if has_files_to_replace:
            msg.append(
                f"Unable to compare some of the modified files with their server version - "
                f"their history will be lost if uploaded."
            )

        if has_unfinished_pull:
            msg.append(
                f"The previous pull has not finished completely: status " f"of some files may be reported incorrectly."
            )

        return msg

    def check_any_changes(self, pull_changes, push_changes):
        if not sum(len(v) for v in list(pull_changes.values()) + list(push_changes.values())):
            root_item = QStandardItem("No changes")
            self.model.appendRow(root_item)

    def add_content(self, changes, root_text, is_server, changes_summary={}):
        """
        Adds rows with changes info
        :param changes: Dict of added/removed/updated/renamed changes
        :param root_text: Text for the root item
        :param is_server: True if changes are related to server file changes
        :param changes_summary: If given and non empty, extra rows are added from geodiff summary.
        :return:
        """
        if all(not changes[k] for k in changes):
            return

        root_item = QStandardItem(root_text)
        self.model.appendRow(root_item)
        for category in changes:
            for file in changes[category]:
                path = file["path"]
                item = self._get_icon_item(category, path)
                if is_versioned_file(path):
                    if path in changes_summary:
                        for sub_item in self._versioned_file_summary_items(changes_summary[path]["geodiff_summary"]):
                            item.appendRow(sub_item)
                    elif not is_server and category != "added":
                        item.appendRow(QStandardItem("Unable to detect changes"))
                        msg = f"Mergin Maps plugin: Unable to detect changes for {path}"
                        QgsApplication.messageLog().logMessage(msg)
                        if self.mp is not None:
                            self.mp.log.warning(msg)
                root_item.appendRow(item)

    def _versioned_file_summary_items(self, geodiff_summary):
        items = []
        for s in geodiff_summary:
            table_name_item = self._get_icon_item("table", s["table"])
            for row in self._table_summary_items(s):
                table_name_item.appendRow(row)
            items.append(table_name_item)

        return items

    def _table_summary_items(self, summary):
        return [QStandardItem("{}: {}".format(k, summary[k])) for k in summary if k != "table"]

    def _get_icon_item(self, key, text):
        path = icon_path(self.icons[key])
        item = QStandardItem(text)
        item.setIcon(QIcon(path))
        return item

    def show_validation_results(self):
        map_layers = QgsProject.instance().mapLayers()

        html = []

        # separate MultipleLayersWarning and SingleLayerWarning items
        groups = dict()
        for k, v in groupby(
            self.validation_results, key=lambda x: "multi" if isinstance(x, MultipleLayersWarning) else "single"
        ):
            groups[k] = list(v)

        # first add MultipleLayersWarnings. They are displayed using warning
        # string as a title and list of affected layers
        if "multi" in groups:
            for w in groups["multi"]:
                issue = warning_display_string(w.id)
                html.append(f"<h3>{issue}</h3>")
                if w.layers:
                    items = []
                    for lid in sorted(w.layers, key=lambda x: map_layers[x].name()):
                        layer = map_layers[lid]
                        items.append(f"<li>{layer.name()}</li>")
                    html.append(f"<ul>{''.join(items)}</ul>")

        if "single" in groups:
            # group SingleLayerWarning items by layer in order to display
            # each layer entry with all warnings, related to it
            layers = dict()
            for k, v in groupby(groups["single"], key=lambda x: x.layer_id):
                layers[k] = list(v)

            for lid in sorted(layers):
                html.append(f"<h3>{map_layers[lid].name()}</h3>")
                items = []
                for w in layers[lid]:
                    items.append(f"<li>{warning_display_string(w.warning)}</li>")
                html.append(f"<ul>{''.join(items)}</ul>")

        self.txtWarnings.setHtml("".join(html))

    def show_changes(self):
        if not self.changes_summary:
            self.ui.messageBar.pushMessage("Mergin", "No changes found in the project layers.", Qgis.Info)
            return

        self.close()
        dlg_diff_viewer = DiffViewerDialog()
        dlg_diff_viewer.show()
        dlg_diff_viewer.exec_()
