# OhMyCaptcha upstream

- Repository: https://github.com/shenhao-stu/ohmycaptcha
- Imported commit: `0b543d5436700fa3455e634583e2642a8a64159f`
- Import strategy: `git subtree --squash`
- Local modifications inside this directory: none

Update procedure:

1. Review upstream changelog, requirements, license and security notes.
2. Run the upstream test suite against the candidate SHA.
3. Run `git subtree pull --prefix=vendor/ohmycaptcha https://github.com/shenhao-stu/ohmycaptcha.git 0b543d5436700fa3455e634583e2642a8a64159f --squash` for the current approved snapshot; replace that argument only with another reviewed full 40-character SHA.
4. Run gateway upstream-contract, Python, Compose and manual live tests.
5. Record the new SHA in this file and in the commit body.
