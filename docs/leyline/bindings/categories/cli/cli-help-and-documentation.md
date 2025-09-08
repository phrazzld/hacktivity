---
id: cli-help-and-documentation
last_modified: '2025-06-14'
version: '0.2.0'
derived_from: explicit-over-implicit
enforced_by: help text review & user testing
---
# Binding: Provide Self-Documenting Help Systems with Progressive Disclosure

Design help systems that make CLI tools self-documenting, with information organized by user intent and expertise level. Users should be able to discover functionality and understand usage without external documentation.

## Rationale

This binding implements our explicit-over-implicit tenet by making CLI functionality and usage patterns discoverable within the tool itself. When users must leave the command line to consult external documentation, workflow disruption occurs and adoption barriers increase. Self-documenting tools reduce cognitive load and enable users to remain in their productive flow state.

Think of CLI help like a well-designed kitchen. In a good kitchen, tools are organized logically, frequently-used items are within easy reach, and specialized equipment is clearly labeled with usage instructions. Users can cook efficiently without constantly consulting cookbooks. Similarly, well-designed CLI help puts essential information at users' fingertips while making advanced features discoverable when needed.

This binding also supports our empathize-with-your-user tenet by recognizing that CLI users often work in terminal-only environments where external documentation access may be limited or disruptive.

## Rule Definition

This binding establishes requirements for CLI help systems:

- **Multi-Level Help Structure**: Provide help at appropriate granularity:
  - Global help: `tool --help` or `tool help`
  - Command help: `tool command --help`
  - Subcommand help: `tool command subcommand --help`
  - Context-sensitive help based on current state

- **Help Content Requirements**: Include essential information in help text:
  - Brief command description (one line)
  - Usage patterns with placeholders
  - Flag descriptions with default values
  - Practical examples for common use cases
  - Related commands or next steps

- **Discovery Mechanisms**: Make functionality discoverable:
  - Command listing: `tool help` shows available commands
  - Suggestion system for typos: "Did you mean 'deploy'?"
  - Category organization for tools with many commands
  - Search functionality for complex tools

- **Format Standards**: Consistent help text formatting:
  - Standard sections: Usage, Description, Options, Examples
  - Consistent indentation and spacing
  - Highlight syntax: bold for commands, italics for placeholders
  - Reasonable line length for terminal display

## Implementation

Create help systems that guide users effectively:

1. **Hierarchical Organization**: Structure help to match user mental models:
   - Group related commands together
   - Show most common commands first
   - Provide category-based organization for large command sets

2. **Example-Driven Help**: Include realistic examples in help text:
   - Show complete command examples, not just syntax
   - Demonstrate common flag combinations
   - Include examples for different scenarios

3. **Interactive Help**: Consider interactive discovery mechanisms:
   - Tab completion for commands and flags
   - Interactive prompts for required parameters
   - Guided workflows for complex operations

4. **Context Awareness**: Provide relevant help based on current state:
   - Show relevant commands based on project context
   - Suggest next steps after command completion
   - Display status-specific help options

## Anti-patterns

- **Minimal Help**: Help that only shows command syntax without explanation
- **External Dependencies**: Requiring users to consult websites or man pages for basic usage
- **Inconsistent Formatting**: Different help formats across commands
- **Information Overload**: Dumping all options without organization or priority
- **Stale Help**: Help text that doesn't match current functionality

## Enforcement

This binding should be enforced through:

- **Help Text Reviews**: Regular audits of help content for accuracy and usefulness
- **User Testing**: Testing help system with new users
- **Documentation Consistency**: Ensuring help text matches external documentation
- **Automated Testing**: Validating that help text includes required elements

## Exceptions

Acceptable variations in help system design:

- **Simple Tools**: Very focused tools may have minimal help requirements
- **Expert Tools**: Domain-specific tools may assume specialized knowledge
- **Interactive Mode**: Tools with interactive modes may provide different help patterns
- **Legacy Compatibility**: Existing tools may maintain historical help patterns

Ensure basic discoverability even with simplified help systems.

## Related Bindings

- [command-interface-design](../../docs/bindings/categories/cli/command-interface-design.md): Command patterns that help systems should document
- [cli-error-handling](../../docs/bindings/categories/cli/cli-error-handling.md): Error messages that guide toward help
- [unified-documentation](../../core/unified-documentation.md): Consistency between CLI help and external docs
