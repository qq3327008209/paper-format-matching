import sys
import gui
from PySide6.QtWidgets import QApplication, QLabel

app = QApplication.instance() or QApplication(sys.argv)
w = gui.MainWindow()
w.resize(1280, 840)
w.show()
app.processEvents()

# 1) 报告页打开/删除按钮宽度
print("=== 报告页打开/删除按钮（需要先有数据）===")
# 模拟添加一行数据
w.r_table.setRowCount(1)
w.r_table.setItem(0, 0, gui.QTableWidgetItem("test.csv"))
w.r_table.setItem(0, 1, gui.QTableWidgetItem("2024-01-01"))
w.r_table.setItem(0, 2, gui.QTableWidgetItem("1.2"))
cell = gui.QWidget()
lay = gui.QHBoxLayout(cell); lay.setContentsMargins(4, 2, 4, 2); lay.setSpacing(6)
open_btn = w._make_btn("打开", "btn-sm btn-success"); open_btn.setMinimumWidth(64)
del_btn = w._make_btn("删除", "btn-sm btn-danger"); del_btn.setMinimumWidth(64)
lay.addWidget(open_btn); lay.addWidget(del_btn); lay.addStretch(1)
w.r_table.setCellWidget(0, 3, cell)
app.processEvents()
print(f"  open_btn: width={open_btn.width()} sizeHint={open_btn.sizeHint().width()}")
print(f"  del_btn: width={del_btn.width()} sizeHint={del_btn.sizeHint().width()}")

# 2) 顶栏 logo 和状态背景
print("\n=== 顶栏 QLabel 背景 ===")
for name in ["header_dot", "header_status"]:
    b = getattr(w, name)
    pix = b.grab()
    img = pix.toImage()
    cx, cy = pix.size().width() // 2, pix.size().height() // 2
    bg = img.pixelColor(cx, cy)
    print(f"  {name}: 中心=({bg.red()},{bg.green()},{bg.blue()}) size={pix.size().width()}x{pix.size().height()}")

# 找顶栏的 HeaderLogo
logo = w.findChild(QLabel, "HeaderLogo")
if logo:
    pix = logo.grab()
    img = pix.toImage()
    cx, cy = pix.size().width() // 2, pix.size().height() // 2
    bg = img.pixelColor(cx, cy)
    print(f"  HeaderLogo: 中心=({bg.red()},{bg.green()},{bg.blue()}) text={logo.text()!r}")

# 3) 底部状态栏
print("\n=== 底部状态栏 QLabel 背景 ===")
for name in ["sb_left", "sb_right"]:
    b = getattr(w, name)
    pix = b.grab()
    img = pix.toImage()
    cx, cy = pix.size().width() // 2, pix.size().height() // 2
    bg = img.pixelColor(cx, cy)
    print(f"  {name}: 中心=({bg.red()},{bg.green()},{bg.blue()}) text={b.text()!r}")
