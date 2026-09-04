# Vaiz Code Spike

A self-hosted coding agent runner is a service that listens for tasks (e.g. from a project tracker),
spins up an isolated environment with a fresh clone of the target repository, and lets an AI coding
agent implement the requested changes autonomously. It applies the changes as commits on a dedicated
branch, then opens a pull request for human review, without ever pushing directly to protected branches.
Running it self-hosted keeps source code and credentials inside the organization's own infrastructure
instead of relying on a third-party SaaS runner.
