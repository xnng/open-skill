---
name: feishu-to-showdoc
description: '将飞书文档（含图片和表格）同步到 ShowDoc。当用户说「同步到 ShowDoc」「飞书同步 ShowDoc」「把飞书文档写到 ShowDoc」「飞书文档同步」或提供飞书链接并要求同步到文档平台时触发。'
---

# 飞书文档同步到 ShowDoc

将飞书云文档的内容（含图片、表格）同步到 ShowDoc 文档平台。

## 前置条件

同步需要认证信息和目标目录。认证信息存储在本 skill 目录下的 `credentials.json` 中（与本 SKILL.md 同级）。

### credentials.json 结构

```json
{
  "showdoc_base_url": "https://your-showdoc.example.com",
  "showdoc_user_token": "<用于图片上传的用户认证 token>",
  "projects": {
    "<项目标识>": {
      "api_key": "<ShowDoc Open API Key>",
      "api_token": "<ShowDoc Open API Token>"
    }
  }
}
```

### 首次使用交互流程（必须严格执行）

执行同步前，先检查本 skill 目录下是否存在 `credentials.json`。如果不存在或缺少必要信息，**必须使用 AskUserQuestion 工具逐项询问，不能跳过**：

1. **ShowDoc 站点地址**（仅首次）：
   - 询问：「请提供你的 ShowDoc 站点地址（如 https://docs.example.com）」
   - 存入 `showdoc_base_url`

2. **ShowDoc 用户 Token**（仅首次）：
   - 询问：「请提供 ShowDoc 的 user_token，用于上传图片。获取方式：用浏览器登录 ShowDoc → F12 打开控制台 → 输入 `JSON.parse(localStorage.getItem('userinfo')).user_token` → 复制返回值」
   - 存入 `showdoc_user_token`

3. **项目 API 凭据**（每个 ShowDoc 项目/知识库独立）：
   - 询问：「请提供目标 ShowDoc 项目的 API Key 和 API Token。获取方式：进入目标项目 → 点击右上角「...」菜单 → 点击「开放 API」→ 复制 API Key 和 API Token」
   - 同时询问一个项目标识（如「操作手册」），用作存储 key
   - 存入 `projects.<项目标识>.api_key` 和 `projects.<项目标识>.api_token`

4. **同步目标目录**：
   - 询问：「请问文档要存放在 ShowDoc 的哪个目录下？（支持多级目录，用 `/` 分隔，如 `智慧能源SAAS系统/运维端`）」

**所有信息确认完毕后，将凭据写入 credentials.json，才能开始同步。** 后续使用时直接读取，只需询问目标目录。

## 同步流程

### 第一步：获取飞书文档

使用飞书 MCP 工具获取文档内容。工具名取决于用户配置的 MCP 服务器名称，通常为 `fetch-doc`，参数为飞书文档 URL 或 ID。

如果找不到飞书 MCP 工具，提示用户检查 `~/.claude.json` 中是否配置了飞书 MCP 服务器。

### 第二步：获取嵌入表格

飞书文档中嵌入的电子表格以 `<sheet token="xxx"/>` 标签表示，token 格式为 `{spreadsheet_token}_{sheet_id}`。

如果飞书 MCP 提供了 `read-sheet` 工具，用它获取表格数据：

```
工具: read-sheet
参数: spreadsheet_token = <下划线前的部分>, range = <下划线后的部分>
```

返回的 `values` 是二维数组，第一行为表头。转为 Markdown 表格：

```markdown
| 表头1 | 表头2 |
| --- | --- |
| 数据1 | 数据2 |
```

如果没有 `read-sheet` 工具，保留占位符 `[表格：请从飞书原文档获取]`。

### 第三步：转换 Markdown

飞书返回的 Markdown 包含特有标签，需要转换为标准 Markdown：

| 飞书标签 | 转换规则 |
|---------|---------|
| `<image token="xxx" .../>` | 替换为 ShowDoc 图片 URL（见第四步） |
| `<sheet token="xxx"/>` | 替换为第二步获取的 Markdown 表格 |
| `<quote-container>...</quote-container>` | 转换为 blockquote `> ` |
| `<grid>`, `<column>` | 直接移除标签，保留内容 |
| `<text bgcolor="xxx">content</text>` | 保留 content，移除标签 |

### 第四步：同步图片

飞书图片使用内部 token，无法直接在外部访问，必须下载后重新上传到 ShowDoc。

使用本 skill 目录下的 `scripts/sync_images.py` 脚本批量处理：

```bash
python3 <本skill目录>/scripts/sync_images.py \
  --input <飞书原始 markdown 文件路径> \
  --credentials <本skill目录>/credentials.json \
  --project <项目标识> \
  --output <输出 markdown 文件路径>
```

脚本会：
1. 从 Markdown 中提取所有 `<image token="xxx">` 的 token
2. 通过飞书 MCP HTTP 接口下载每张图片（base64）
3. 上传到 ShowDoc（通过 `uploadImg` API）
4. 将 `<image>` 标签替换为 `![image](ShowDoc URL)`
5. 同时完成其他 Markdown 标签的转换
6. 输出最终的标准 Markdown 文件

脚本从 `~/.claude.json` 中自动读取飞书 MCP 服务器的 URL 和 API Key。如果找不到，提示用户检查 MCP 配置。

### 第五步：推送到 ShowDoc

使用 ShowDoc Open API 创建或更新文档：

```bash
curl -s -X POST '<showdoc_base_url>/server/index.php?s=/api/open/updatePage' \
  -d "api_key=<api_key>" \
  -d "api_token=<api_token>" \
  -d "cat_name=<目标目录>" \
  --data-urlencode "page_title=<文档标题>" \
  --data-urlencode "page_content@<转换后的 markdown 文件>" \
  -d "s_number=1"
```

- `page_title`：使用飞书文档的标题
- `cat_name`：用户指定的目标目录
- `updatePage` 接口按标题匹配：已存在则更新，不存在则创建

### 第六步：验证

同步完成后，输出 ShowDoc 文档的访问地址，格式为：
`<showdoc_base_url>/web/#/<item_id>/<page_id>`

其中 `item_id` 和 `page_id` 从 API 响应中获取。

## 注意事项

- 单次同步的图片数量没有限制，脚本会逐张处理，个别失败会自动重试一次
- ShowDoc 的 `updatePage` 会对内容做 `htmlspecialchars` 处理，这是 ShowDoc 的内置行为，不影响最终渲染
- 飞书有序列表全部使用 `1.` 标记（依赖渲染器自动编号），转换时需要替换为正确的递增编号
