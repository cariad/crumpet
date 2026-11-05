# Contributing

## Requirements

- [mise](https://mise.jdx.dev/)

## Setup

1. Clone the repository.

   ```bash
   git clone git@github.com:cariad/crumpet.git
   ```

1. Change into the repository's directory.

   ```bash
   cd crumpet
   ```

1. Install the development tools.

   ```bash
   mise install
   ```

1. Initialise the Python environment.

   ```bash
   mise exec -- uv sync --locked
   ```

## Linting

```bash
mise run lint
```

## Testing

```bash
mise run test
```

## AI policy

AI-assisted contributions are welcomed.
