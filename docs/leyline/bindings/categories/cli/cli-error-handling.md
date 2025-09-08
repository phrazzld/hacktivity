---
id: cli-error-handling
last_modified: '2025-06-14'
version: '0.2.0'
derived_from: fix-broken-windows
enforced_by: error message review & user testing
---
# Binding: Provide Actionable Error Messages with Clear Recovery Paths

Design error messages that help users understand what went wrong and what to do next. Error messages should be human-readable, actionable, and guide users toward successful completion of their intended task.

## Rationale

This binding implements our fix-broken-windows tenet by preventing the degradation of user experience that occurs when errors are cryptic, unhelpful, or frustrating. Poor error messages create negative feedback loops where users avoid features, work around tools, or abandon tasks entirely. When errors provide clear guidance, they become learning opportunities rather than roadblocks.

Think of error messages like emergency exit signs in a building. In a crisis, people need clear, immediate direction to safety—not technical diagrams or building codes. Similarly, when CLI operations fail, users need immediate, actionable guidance to resolve the problem and continue their work. Just as unclear emergency signage can cause panic and injury, unclear error messages cause user frustration and lost productivity.

This binding also supports our empathize-with-your-user tenet by recognizing that errors often occur during high-pressure situations where users need quick resolution paths.

## Rule Definition

This binding establishes requirements for CLI error handling:

- **Error Message Structure**: Include essential information in every error:
  - Clear description of what went wrong
  - Context about what the tool was attempting
  - Specific action the user can take to resolve the issue
  - Reference to relevant help or documentation when appropriate

- **Error Classification**: Use consistent patterns for different error types:
  - User errors: Configuration problems, invalid inputs, missing prerequisites
  - System errors: Network failures, permission issues, resource constraints
  - Tool errors: Internal failures, unexpected states, dependency problems

- **Exit Code Standards**: Use conventional exit codes for automation:
  - `0`: Success
  - `1`: General application error
  - `2`: Misuse (invalid arguments, etc.)
  - `126`: Command found but not executable
  - `127`: Command not found
  - Domain-specific codes for specialized tools

- **Progressive Error Detail**: Provide appropriate detail levels:
  - Concise error by default
  - Verbose mode for debugging (`--verbose`, `--debug`)
  - Stack traces only when relevant for user action
  - Correlation IDs for complex systems

## Implementation

Create error messages that guide users effectively:

1. **Plain Language**: Write errors in natural language, not technical jargon:
   - ✅ "Configuration file 'config.yml' not found in current directory"
   - ❌ "ENOENT: no such file or directory, open 'config.yml'"

2. **Specific Actions**: Tell users exactly what to do:
   - ✅ "Run 'tool init' to create a configuration file"
   - ❌ "Configuration required"

3. **Context Preservation**: Include relevant context in error messages:
   - Show the command that failed
   - Include relevant file paths or identifiers
   - Display current state when relevant

4. **Recovery Guidance**: Provide clear next steps:
   - Suggest specific commands to run
   - Link to relevant help sections
   - Offer automatic fixes when safe

## Anti-patterns

- **Cryptic Error Codes**: Errors like "Error 2147483647" without explanation
- **Technical Dumps**: Stack traces or internal errors exposed to users
- **Blame Language**: Messages that make users feel incompetent
- **Dead Ends**: Errors that don't suggest any resolution path
- **Inconsistent Formatting**: Different error formats across the application

## Enforcement

This binding should be enforced through:

- **Error Message Audits**: Regular review of error handling paths
- **User Testing**: Testing error scenarios with actual users
- **Error Analytics**: Tracking which errors occur most frequently
- **Documentation Consistency**: Ensuring error messages align with help text

## Exceptions

Acceptable variations in error handling:

- **Debug Mode**: Development builds may show more technical detail
- **Expert Tools**: Specialized tools may assume domain knowledge
- **Legacy Integration**: Tools interfacing with legacy systems may preserve some technical errors
- **Performance Critical**: High-performance tools may use abbreviated errors

Always provide at least basic context and suggested actions.

## Related Bindings

- [command-interface-design](../../docs/bindings/categories/cli/command-interface-design.md): Consistent patterns that reduce user errors
- [cli-help-and-documentation](../../docs/bindings/categories/cli/cli-help-and-documentation.md): Help systems that errors can reference
- [explicit-over-implicit](../../core/explicit-over-implicit.md): Making failures and their causes visible
