---
name: feishu-to-showdoc
description: "将飞书文档及其图片、画板、嵌入表格同步到 ShowDoc。"
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

### 认证与目标

先检查已有 `credentials.json`、用户指令和 `sync-tasks/` 配置，只询问缺失的认证字段或无法确定的目标目录，已有信息不重复确认。认证写入上述结构，临时目录选择仅用于当前任务。

ShowDoc 的 user_token 可从已登录站点的浏览器 localStorage 中 `userinfo.user_token` 获取；项目 API Key/Token 位于项目的「开放 API」。不具备所需权限时停止同步。

## 预定义同步任务

本 skill 目录下的 `sync-tasks/` 存放预定义的批量同步任务配置。当用户使用简短指令（如「同步操作手册到 ShowDoc」）时，先在 `sync-tasks/` 中查找匹配的配置文件，找到则按配置批量执行，跳过第 4 步的目录询问。

### 配置文件格式

```json
{
  "name": "任务名称",
  "project": "credentials.json 中的项目标识",
  "feishu_root": "飞书知识库根节点 URL",
  "documents": [
    {
      "doc_id": "飞书文档 ID",
      "title": "文档标题",
      "cat_name": "ShowDoc 目标目录"
    }
  ]
}
```

### 批量同步执行逻辑

1. 读取配置文件中的 `documents` 列表
2. 对每个文档依次执行：获取飞书内容 → 处理图片 → 推送到 ShowDoc
3. 使用 `scripts/sync_images.py` 处理图片和 Markdown 转换
4. 推送时如果 curl 超时（大文档），增加 `--max-time` 到 120 秒重试
5. 输出汇总报告：成功/失败数量及失败文档列表

## 同步流程

### 第一步：获取飞书文档

使用 `lark-cli docs +fetch --doc "飞书文档 URL 或 ID"`（详见 lark-doc skill）获取文档内容。

### 第二步：获取嵌入表格

飞书文档中嵌入的电子表格以 `<sheet token="xxx"/>` 标签表示，token 格式为 `{spreadsheet_token}_{sheet_id}`。

同步时必须识别并展开这类嵌入 Sheet，禁止保留 `<sheet token="..."/>`、`本地 Markdown 未展开` 或仅保留 token 的占位。使用 `lark-cli sheets +read`（详见 lark-sheets skill）获取表格数据：

```bash
lark-cli sheets +read --spreadsheet-token <最后一个下划线前的部分> --sheet-id <最后一个下划线后的部分>
```

返回的 `values` 是二维数组，第一行为表头。单元格可能是普通字符串、数字、空值，也可能是富文本 segment 数组；转换前必须先拍平成文本。然后转为 Markdown 表格：

```markdown
| 表头1 | 表头2 |
| --- | --- |
| 数据1 | 数据2 |
```

如果 `sheets +read` 失败，必须让同步校验失败并停止推送，不允许静默推送缺表内容。

### 第三步：转换 Markdown

飞书返回的 Markdown 包含特有标签，需要转换为标准 Markdown：

| 飞书标签 | 转换规则 |
|---------|---------|
| `<image token="xxx" .../>` | 替换为 ShowDoc 图片 URL（见第四步） |
| `<lark-table>...<lark-tr><lark-td>` | 转换为 Markdown 表格，第一行为表头 |
| `<sheet token="xxx"/>` | 替换为第二步获取的 Markdown 表格 |
| `<quote-container>...</quote-container>` | 转换为 blockquote `> ` |
| `<callout ...>...</callout>` | 转换为 blockquote `> ` |
| `<grid>`, `<column>` | 直接移除标签，保留内容 |
| `<text bgcolor="xxx">content</text>` | 保留 content，移除标签 |
| `<whiteboard token="xxx"/>` | 下载画板图片后替换为 ShowDoc 图片 URL（见第四步） |

### 第四步：同步图片和画板

飞书图片和画板使用内部 token，无法直接在外部访问，必须下载后重新上传到 ShowDoc。脚本会自动识别 `<image>` 和 `<whiteboard>` 标签并分别处理（画板使用 `type: "whiteboard"` 参数下载）。

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
2. 通过 `lark-cli docs +media-download` 下载每张图片到本地
3. 上传到 ShowDoc（通过 `uploadImg` API）
4. 将 `<image>` 标签替换为 `![image](ShowDoc URL)`
5. 从 `<sheet token="spreadsheetToken_sheetId"/>` 读取嵌入 Sheet，并展开为标准 Markdown 表格
6. 同时完成其他 Markdown 标签的转换
7. 输出最终的标准 Markdown 文件
8. 校验 `<lark-table>` 和 `<sheet>` 对应的表格数量一致，且输出中没有未展开 Sheet 或同步失败占位；校验失败时脚本以非 0 状态退出，禁止继续推送 ShowDoc

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
- 飞书有序列表全部使用 `1.` 标记（依赖渲染器自动编号），转换时需要替换为正确的递增编号
- ShowDoc 的 `updatePage` 接口，`page_title` 和 `page_content` 会经过 ThinkPHP 的 `I()` 函数过滤（默认 `htmlspecialchars`），以及 PageModel 的 `_htmlspecialchars` 方法处理。已通过以下修改解决特殊字符转义问题（如 `&` 被存为 `&amp;`）：
  - `OpenController.class.php`：`I("page_title", '', '')` 和 `I("page_content", '', '')`，第三个参数为空字符串以禁用默认过滤
  - `PageModel.class.php`：`page_title` 不再经过 `_htmlspecialchars`，直接存储原始值
  - 修改后需部署到服务器：`scp` 文件到 `/data/showdoc/server/Application/` 对应路径，然后 `docker restart showdoc`

## 开源仓库同步

本 skill 的公开镜像位于 `/Users/xnng/code/utils/open-skill/skills/feishu-to-showdoc/`。修改时核对并同步对应内容，保留镜像中的既有改动；不复制凭据或内部任务配置。提交和推送按当前任务的明确授权执行，维护本地 skill 不自动授权发布。
