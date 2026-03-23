# Open Skill

个人开源的 AI Agent Skills，兼容 [Vercel Skills CLI](https://www.npmjs.com/package/skills)。

## 安装

```bash
# 安装全部 skills
npx skills add xnng/open-skill -g -a claude-code

# 安装指定 skill
npx skills add xnng/open-skill -g -a claude-code --skill feishu-to-showdoc
```

## Skills 列表

| Skill | 说明 |
|-------|------|
| [feishu-to-showdoc](skills/feishu-to-showdoc) | 将飞书文档（含图片和表格）同步到 ShowDoc |
