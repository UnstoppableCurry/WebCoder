# 贡献指南

感谢您对 WebCoder 的关注！我们欢迎各种形式的贡献。

## 🚀 如何贡献

### 报告问题

1. 使用 GitHub Issues 报告问题
2. 提供详细的复现步骤
3. 包含环境信息（操作系统、Python版本等）
4. 如有错误信息，请提供完整的错误日志

### 提交代码

1. **Fork 项目**
   ```bash
   git clone https://github.com/yourusername/webcoder.git
   cd webcoder
   ```

2. **创建分支**
   ```bash
   git checkout -b feature/your-feature-name
   # 或
   git checkout -b fix/bug-description
   ```

3. **开发代码**
   - 遵循 PEP 8 代码规范
   - 添加必要的注释
   - 确保通过所有测试

4. **提交更改**
   ```bash
   git add .
   git commit -m "feat: 添加新功能描述"
   ```

5. **推送并创建 PR**
   ```bash
   git push origin feature/your-feature-name
   ```
   然后在 GitHub 上创建 Pull Request

## 📋 代码规范

### Python 代码规范

- 遵循 PEP 8
- 使用 4 空格缩进
- 最大行长度 100 字符
- 使用有意义的变量名

```python
# 好的示例
def create_project(name: str, mode: str = "new") -> dict:
    """创建新项目"""
    project = {
        "name": name,
        "mode": mode,
        "created_at": datetime.now()
    }
    return project

# 不好的示例
def cp(n,m="new"):
    p={"n":n,"m":m}
    return p
```

### 提交信息规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

- `feat:` 新功能
- `fix:` 修复bug
- `docs:` 文档更新
- `style:` 代码格式调整
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具相关

示例：
```
feat: 添加 Kimi 会话续传支持
fix: 修复字体设置无法保存的问题
docs: 更新安装指南
```

## 🧪 测试

在提交 PR 之前，请确保：

1. 代码可以正常运行
2. 没有明显的 bug
3. 文档已更新（如需要）

## 📞 联系我们

- GitHub Issues: [提交问题](https://github.com/yourusername/webcoder/issues)
- 邮件: your-email@example.com

再次感谢您的贡献！