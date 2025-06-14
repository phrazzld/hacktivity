```text
## Technical Overview: /Users/phaedrus/Development/hacktivity/hacktivity/prompts

**Purpose:**

This directory, `/Users/phaedrus/Development/hacktivity/hacktivity/prompts`, serves as a repository for prompt templates used within the Hacktivity project. These prompts are designed to guide and instruct a language model (likely a large language model or LLM) to perform specific tasks related to summarizing and analyzing engineering activity, particularly based on git commit history. The prompts aim to automate or assist in generating reports and summaries for various purposes, such as team retrospectives, daily stand-ups, and weekly newsletters.

**Architecture:**

The directory follows a simple, flat structure. Each file within the directory represents a distinct prompt template tailored for a specific reporting or analysis context. The files are named descriptively to indicate their intended use (e.g., `retro.md` for retrospective prompts). The content of each file is a Markdown-formatted string containing instructions and guidance for the language model.  This structure allows for easy organization and modification of the prompts without requiring code changes.

**Key File Roles:**

*   **`retro.md`:** This file contains the prompt template used to guide the language model in analyzing git commit messages for the purpose of preparing for a team retrospective. It instructs the model to identify themes, potential struggles, and major accomplishments, and to structure the output in a specific Markdown format.

*   **`standup.md`:** This file contains the prompt template for generating a summary of accomplishments suitable for a daily stand-up meeting. It instructs the language model to focus on impact and outcomes, rephrase commit messages into a professional tone, and create a concise, bulleted list.

*   **`weekly.md`:** This file provides the prompt template for creating a weekly team newsletter summary. It focuses on major features, technical improvements, and team collaboration, requiring the language model to generate a brief narrative with bullet points.

**Dependencies and Gotchas:**

*   **Markdown Formatting:** The prompts rely on Markdown formatting. The language model must be capable of generating output that adheres to the specified Markdown structure.

*   **Git Commit Message Quality:** The effectiveness of these prompts heavily depends on the quality and clarity of the git commit messages. Poorly written or vague commit messages will likely result in less accurate and less useful summaries.

*   **Language Model Capabilities:** The success of these prompts relies on the language model's ability to understand and follow instructions, synthesize information, and generate coherent and grammatically correct text.  The specific capabilities of the chosen language model will significantly impact the quality of the generated summaries.

*   **Context Window Limits:** Depending on the number of git commit messages fed into the language model, the input might exceed the model's context window limit. This could lead to truncation or incomplete analysis. Strategies for handling large commit histories may be necessary.
```