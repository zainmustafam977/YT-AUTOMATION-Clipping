# 🤝 Contributing to the YouTube Shorts Automation Engine

Thank you for contributing to this enterprise platform. To maintain our high standards of software craftsmanship, stability, and reliability, all engineers and automated agents must adhere to the following guidelines.

---

## 🌿 Branching Strategy

We follow a GitFlow-inspired branching model:

- **`main`**: Production-ready, authoritative branch. Never commit directly to `main` without review.
- **`develop`**: Primary integration branch for upcoming features.
- **`feat/<feature-name>`**: Feature branches branched from `develop`.
- **`fix/<bug-description>`**: Bug fix branches branched from `develop` or `main`.
- **`refactor/<component>`**: Refactoring branches with strict zero-regression guarantees.

---

## 📜 Commit Message Conventions

We enforce [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <short description>

[optional body explaining rationale]

[optional footer(s)]
```

### Types:
- `feat`: New feature or capability.
- `fix`: Bug fix or error resolution.
- `docs`: Documentation updates only.
- `refactor`: Code restructuring without changing external behavior.
- `test`: Adding or updating test suites.
- `chore`: Build processes, dependencies, or tool configurations.

---

## 🧪 Testing & Verification Gates

Before submitting a Pull Request, you must verify:
1. **Linting & Code Style**:
   ```bash
   ruff check .
   mypy shorts_engine
   ```
2. **Complete Test Suite Execution**:
   ```bash
   pytest tests -q
   ```
   All 337+ unit and integration tests must pass with 100% success rate.
3. **No Unencrypted Secrets in Code**:
   Do not commit plaintext credentials in source files. Utilize `.env` or the `KeyVault` service.

---

## 📋 Pull Request Checklist

When opening a Pull Request:
- [ ] Meaningful PR title adhering to Conventional Commits.
- [ ] Detailed description explaining *why* the change is made and *what* it impacts.
- [ ] New unit tests covering edge cases and failure paths.
- [ ] Verification on both Windows and Linux platforms if interacting with system APIs.
- [ ] Zero linting errors and 100% test pass rate.
