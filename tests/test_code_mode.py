# -*- coding: utf-8 -*-
"""
代码模式验证：用一个确定性的"编辑器模型"跑一遍按键步骤，比对最终文本。

模型实现的编辑器语义（对齐 VS Code / Visual Studio / CLion 的默认行为）：
  · 打 ( [ { " ' 自动补一个闭合符，光标停在中间
  · 打闭合符时若光标右边就是它 → 直接跨过去（type-over），不重复插入
  · 回车按当前行缩进 + 4 空格自动缩进
  · 光标右边是 } 时回车会展开成三行，把补出来的 } 推到下一行
  · Home 智能跳转：先到行首第一个非空白，再按一次到第 0 列

这些语义正是逐字输入最容易踩坑的地方，用它来验证 build_steps 的策略是否成立。

用法：python tests/test_code_mode.py [--live]
      --live 额外做一次真实注入冒烟测试（会弹一个窗口抢焦点）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typewriter_ime import (          # noqa: E402
    VK_DELETE, VK_END, VK_HOME, VK_RETURN, VK_RIGHT, VK_TAB,
    build_steps, resolve_indent,
)

INDENT = "    "
OPENERS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
CLOSERS = set(OPENERS.values())


class EditorModel:
    """只实现与逐字输入相关的编辑器行为，其余一概不管。"""

    def __init__(self, auto_pair=True, auto_indent=True, block_expand=True):
        self.buf: list[str] = []
        self.cur = 0
        self.sel: tuple[int, int] | None = None
        self.auto_pair = auto_pair
        self.auto_indent = auto_indent
        self.block_expand = block_expand

    # ---- 文本工具 ----
    def text(self) -> str:
        return "".join(self.buf)

    def line_start(self) -> int:
        i = self.cur
        while i > 0 and self.buf[i - 1] != "\n":
            i -= 1
        return i

    def line_end(self) -> int:
        i = self.cur
        while i < len(self.buf) and self.buf[i] != "\n":
            i += 1
        return i

    def insert(self, s: str):
        if self.sel:                       # 有选中内容 → 输入直接替换它
            a, b = self.sel
            del self.buf[a:b]
            self.cur = a
            self.sel = None
        self.buf[self.cur:self.cur] = list(s)
        self.cur += len(s)

    # ---- 按键 ----
    def key_char(self, ch: str):
        if self.auto_pair and ch in OPENERS:
            self.insert(ch + OPENERS[ch])
            self.cur -= 1
        elif self.auto_pair and ch in CLOSERS:
            if self.cur < len(self.buf) and self.buf[self.cur] == ch:
                self.cur += 1                       # type-over
            else:
                self.insert(ch)
        else:
            self.insert(ch)

    def key_return(self):
        if not self.auto_indent:
            self.insert("\n")
            return
        line = "".join(self.buf[self.line_start():self.cur])
        indent = line[:len(line) - len(line.lstrip(" \t"))]
        base = indent + INDENT if line.rstrip().endswith("{") else indent
        if self.block_expand and self.cur < len(self.buf) and self.buf[self.cur] == "}":
            self.insert("\n" + base + "\n" + indent)
            self.cur -= len(indent) + 1
        else:
            self.insert("\n" + base)

    def key_home(self):
        if not self.auto_indent:
            self.cur = self.line_start()
            return
        ls = self.line_start()
        full = "".join(self.buf[ls:self.line_end()])
        first = len(full) - len(full.lstrip(" \t"))
        self.cur = ls + first if self.cur - ls > first else ls

    def key_shift_end(self):
        self.sel = (self.cur, self.line_end())

    def key_delete(self):
        if self.cur < len(self.buf):
            del self.buf[self.cur]

    def play(self, steps) -> str:
        for kind, arg, _w in steps:
            if kind == "char":
                self.key_char(arg)
            elif arg[0] == VK_RETURN:
                self.key_return()
            elif arg[0] == VK_END:
                self.key_shift_end()
            elif arg[0] == VK_HOME:
                self.key_home()
            elif arg[0] == VK_DELETE:
                self.key_delete()
            elif arg[0] == VK_TAB:
                self.insert("\t")
            elif arg[0] == VK_RIGHT:
                self.cur = min(self.cur + 1, len(self.buf))
        return self.text()


MULTILINE = """#include <iostream>
int main() {
    int a[3] = {1, 2, 3};
    std::cout << "hi" << std::endl;
    if (a[0] == 1) {
        return 0;
    }
    return 1;
}"""

SINGLELINE = 'int f() { return g(a[0], {1, 2}); }'

EDGE = """void empty() {}
void blank() {

    int x = 0;
    if (x) {
        for (int i = 0; i < 3; ++i) {
            x += i;
        }
    }
}"""

# 真实案例：从网页复制后行首缩进全部丢失的代码（用户实际遇到的样子）
FLAT = """#include <iostream>
#include <iomanip>
#include <string>
using namespace std;
// Student 类：用成员变量保存数据，用成员函数操作数据
class Student {
private:
string name;   // 姓名
string id;     // 学号
double score;  // 成绩
public:
// 设置学生信息
void setInfo(const string& n, const string& i, double s) {
name = n;
id = i;
score = s;
}
// 输出学生信息（不修改成员，声明为 const 成员函数）
void display() const {
cout << "Name: " << name << endl;
cout << "ID: " << id << endl;
cout << "Score: " << fixed << setprecision(1) << score << endl;
}
};
int main() {
string name, id;
double score;
cin >> name >> id >> score;  // 一行输入：姓名 学号 成绩
Student stu;                 // 创建一个学生对象
stu.setInfo(name, id, score); // 通过对象调用成员函数设置属性
stu.display();                // 通过对象调用成员函数输出信息
return 0;
}"""

WANT = """#include <iostream>
#include <iomanip>
#include <string>
using namespace std;
// Student 类：用成员变量保存数据，用成员函数操作数据
class Student {
private:
    string name;   // 姓名
    string id;     // 学号
    double score;  // 成绩
public:
    // 设置学生信息
    void setInfo(const string& n, const string& i, double s) {
        name = n;
        id = i;
        score = s;
    }
    // 输出学生信息（不修改成员，声明为 const 成员函数）
    void display() const {
        cout << "Name: " << name << endl;
        cout << "ID: " << id << endl;
        cout << "Score: " << fixed << setprecision(1) << score << endl;
    }
};
int main() {
    string name, id;
    double score;
    cin >> name >> id >> score;  // 一行输入：姓名 学号 成绩
    Student stu;                 // 创建一个学生对象
    stu.setInfo(name, id, score); // 通过对象调用成员函数设置属性
    stu.display();                // 通过对象调用成员函数输出信息
    return 0;
}"""


def show_diff(got: str, want: str) -> str:
    gl, wl = got.split("\n"), want.split("\n")
    for i in range(max(len(gl), len(wl))):
        g = gl[i] if i < len(gl) else "<缺行>"
        w = wl[i] if i < len(wl) else "<多出行>"
        if g != w:
            return f"第 {i + 1} 行\n        期望 {w!r}\n        实际 {g!r}"
    return ""


def case(name, src, brackets, indent, expect_equal, **model_kw):
    model = EditorModel(**model_kw)
    got = model.play(build_steps(src, "enter", brackets, indent))
    ok = (got == src) if expect_equal else (got != src)
    label = "应完全一致" if expect_equal else "应暴露问题（对照）"
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  [{label}]")
    if not ok:
        print("      " + show_diff(got, src).replace("\n", "\n      "))
    elif not expect_equal:
        print("      原始问题：" + show_diff(got, src).replace("\n", " / "))
    return ok


def case_indent(name, src, want, mode="auto", **model_kw):
    """缩进丢失的源码 → 按大括号推导 → 逐字输入 → 与期望格式比对。"""
    fixed = resolve_indent(src, mode)
    model = EditorModel(**model_kw)
    got = model.play(build_steps(fixed, "enter", "delete", True))
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  [推导后逐字输入 应完全一致]")
    if not ok:
        print("      " + show_diff(got, want).replace("\n", "\n      "))
    return ok


def live_injection() -> bool:
    """真实注入冒烟测试：往一个普通文本框里打 12 个字符，验证端到端通路。"""
    import time
    import tkinter as tk

    from typewriter_ime import GA_ROOT, execute, user32

    root = tk.Tk()
    root.geometry("520x160+260+260")
    text = tk.Text(root, font=("Consolas", 12))
    text.pack(fill="both", expand=True)
    root.update()
    root.focus_force()
    text.focus_set()
    hwnd = user32.GetAncestor(root.winfo_id(), GA_ROOT)
    payload = "hello world!"

    if user32.GetForegroundWindow() != hwnd:
        print("  SKIP  真实注入：窗口没拿到前台焦点，放弃（避免敲到别的窗口）")
        root.destroy()
        return True

    execute(build_steps(payload), base=30.0, human=False, sleep_fn=time.sleep)

    def check():
        got = text.get("1.0", "end-1c")
        print(f"  {'PASS' if got == payload else 'FAIL'}  真实注入 {got!r}")
        root.destroy()

    root.after(700, check)
    root.mainloop()
    return True


def main() -> int:
    print("代码模式验证（编辑器模型）")
    ok = True

    ok &= case("多行块 + 括号补全 → 删除自动补全", MULTILINE, "delete", True, True)
    ok &= case("多行块 + 不处理（对照：缩进翻倍 / 多出 }）",
               MULTILINE, "off", False, False)
    ok &= case("单行 + 跳过补全", SINGLELINE, "skip", True, True)
    ok &= case("单行 + 删除自动补全", SINGLELINE, "delete", True, True)
    ok &= case("空块 / 空行 / 三层嵌套", EDGE, "delete", True, True)

    print("\n缩进丢失 → 按大括号推导 → 逐字输入")
    ok &= case_indent("真实案例：Student 类（网页复制丢了缩进）", FLAT, WANT)
    ok &= case_indent("同一段代码，编辑器无自动缩进", FLAT, WANT, auto_indent=False)
    ok &= case_indent("已排好版的源码，auto 不重排", WANT, WANT)

    print("\n真实注入")
    if "--live" in sys.argv:
        live_injection()
    else:
        print("  SKIP  未指定 --live，跳过（会弹窗口抢焦点）")

    print("\n结论：" + ("通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
