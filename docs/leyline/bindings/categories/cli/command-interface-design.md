---
id: command-interface-design
last_modified: '2025-06-14'
version: '0.2.0'
derived_from: empathize-with-your-user
enforced_by: usability testing & user feedback
---
# Binding: Design CLI Commands for Human Intuition and Discoverability

Structure command-line interfaces around natural language patterns and common conventions. Commands should be guessable, memorable, and follow established POSIX patterns that users can predict based on experience with other tools.

## Rationale

This binding implements our empathize-with-your-user tenet by recognizing that CLI users are working under time pressure, often in high-stress situations, and need interfaces that align with their mental models and existing knowledge. When commands follow intuitive patterns, users can accomplish tasks quickly without consulting documentation, reducing cognitive overhead and increasing productivity.

Think of CLI design like designing street signs. Good street signs use consistent symbols, readable fonts, and logical placement that drivers can process quickly without stopping. Similarly, well-designed CLIs use familiar patterns, predictable naming, and logical organization that users can navigate efficiently. Just as confusing street signs cause traffic problems, unintuitive CLI designs cause workflow disruption and user frustration.

This binding also supports our simplicity tenet by preferring conventional patterns over innovative interfaces that require learning new paradigms.

## Rule Definition

This binding establishes patterns for CLI command design:

- **Command Structure**: Follow conventional verb-noun or noun-verb patterns:
  - ✅ `git commit`, `docker run`, `kubectl get` (verb-noun)
  - ✅ `npm install`, `brew update` (tool-action)
  - ❌ `processfile`, `handledata` (unclear action)

- **Flag Conventions**: Use standard flag patterns:
  - Single-letter flags: `-h`, `-v`, `-f` for common options
  - Long flags with double dashes: `--help`, `--version`, `--force`
  - Boolean flags don't require values: `--verbose`, not `--verbose=true`
  - Value flags use `=` or space: `--output=json` or `--output json`

- **Subcommand Organization**: Group related functionality logically:
  - Clear hierarchies: `app deploy`, `app logs`, `app scale`
  - Consistent naming: use same verbs across contexts
  - Avoid deep nesting: prefer `tool action target` over `tool category subcategory action`

- **Output Standards**: Provide consistent, parseable output:
  - Human-readable by default
  - Machine-readable formats available (`--format json`)
  - Consistent exit codes (0 for success, non-zero for errors)
  - Predictable column layouts for tabular data

## Implementation

Design commands that users can discover and remember:

1. **Verb Selection**: Choose common, unambiguous verbs:
   - `create`, `delete`, `list`, `show`, `update`
   - Avoid technical jargon: prefer `start` over `initialize`
   - Use consistent verbs across the application

2. **Flag Design**: Prioritize commonly-used flags:
   - Provide short aliases for frequent operations
   - Group related flags logically in help text
   - Use consistent flag names across commands

3. **Help Integration**: Make help discoverable and useful:
   - Support both `-h` and `--help`
   - Provide command-specific help: `tool command --help`
   - Include examples in help text

4. **Progressive Disclosure**: Design for different expertise levels:
   - Simple commands work with minimal flags
   - Advanced options available but not prominent
   - Sensible defaults for most parameters

## Anti-patterns

- **Cryptic Commands**: Single-letter commands or unclear abbreviations
- **Inconsistent Verbs**: Using different verbs for the same action across commands
- **Required Memorization**: Commands that can't be guessed from context
- **Flag Explosion**: Commands with dozens of required flags
- **Unclear Hierarchy**: Subcommands that don't logically group functionality

## Enforcement

This binding should be enforced through:

- **User Testing**: Regular testing with both novice and expert users
- **Convention Compliance**: Automated checks for flag and command patterns
- **Documentation Reviews**: Ensuring commands can be explained simply
- **Comparative Analysis**: Benchmarking against established CLI tools

## Exceptions

Valid deviations from standard patterns:

- **Domain Conventions**: Following established patterns in specific domains (e.g., database CLIs)
- **Legacy Compatibility**: Maintaining compatibility with existing user workflows
- **Performance Requirements**: Optimizing for expert users in specialized contexts
- **Integration Constraints**: Matching patterns required by external systems

Document the reasoning when deviating from conventions.

## Related Bindings

- [cli-help-and-documentation](../../docs/bindings/categories/cli/cli-help-and-documentation.md): Comprehensive help system design
- [cli-error-handling](../../docs/bindings/categories/cli/cli-error-handling.md): User-friendly error messages
- [preferred-technology-patterns](../../core/preferred-technology-patterns.md): CLI framework selection
