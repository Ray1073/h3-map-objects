# 数据来源与许可

本目录下的 JSON 表不是凭空写的，来源分两类：

## 1. 枚举定义 —— h3mtxt（MIT）

`class对照表.json` 的 232 个 class 名，以及 `兵种ID表.json` 的 161 条兵种 ID，
其**枚举名和编号**来自：

> **h3mtxt** — https://github.com/alexanderbelous/h3mtxt
> Copyright (c) 2025-2026 Alexander Belous

该仓库使用 MIT 许可证，全文如下：

```
MIT License

Copyright (c) 2025-2026 Alexander Belous

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

MIT 与本项目使用的 GPL-3.0 兼容（MIT 允许再许可到 GPL），
上述声明在此保留，不因本项目的许可证而失效。

## 2. 中文名与属性 —— 社区数据库整理

`兵种ID表.json` 里的中文名、等级、AI Value，以及 `对象类型表.json` 的全部内容，
来自公开的《英雄无敌3》数据库资料（游戏内帮助文本、社区 wiki）的人工整理。

**这类数据不含游戏美术资源、音频或代码本体**，只是名称与数值事实的汇编。
如果你认为其中某项侵犯了你的权利，请提 issue 说明，我们会移除或改写该条目。

## 3. 本项目自己的部分

`h3objs.py` / `h3map.py` 的代码、以及 README 里描述的所有**存档格式结论**
（各对象的字节布局、偏移、定位方法），都是本项目的逆向分析结果，
按 GPL-3.0 发布（见上级目录的 LICENSE）。
