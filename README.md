# ftpsync

A caching website uploader using FTPS

This is a tool to deploy web sites using FTPS.

## Using with GitHub Actions

This assumes that you have already a GitHub Actions workflow that builds and tests your project.

### Add ftpsync to your project

```bash
git submodule add https://github.com/oefe/ftpsync.git
```

Also, make sure that your workflow checks out submodules:

```yaml
    - name: Checkout
      uses: actions/checkout@v2
      with:
        submodules: true
```

### Create a GitHub Secret to store your FTP password

On GitHub, go to your repository, "Settings", "Secrets". Click "New Secret", enter:

- Name: FTP_PASSWORD
- Value: your FTP password

And click "Add Secret"

### Add a deployment step

Add a deployment step to your GitHub Actions workflow that builds your site:

```yaml
    - name: Deploy
      run: ftpsync/ftpsync.py example.com --user myself --password "${{ secrets.FTP_PASSWORD }}"
```

This should come after the build and tests steps.

- Replace "example.com" with the hostname of your FTP server.
- Replace "myself" with your FTP username.

- Depending on your build process, you may also have to specify the source directory using the `--source` option.
The default ("public") is suitable for the [Hugo](gohugo.io) static site generator.

- Depending on your hosting provider, you may also have to specify the destination directory using the `--destination` option.
The default is "html".

### Commit any push your changes

When you are ready, commit and push your changes.

```bash
git commit -a -m "Add deployment via ftpsync"
```

Your GitHub Actions workflow should now deploy your site automatically.

The first deployment may take a while, as `ftpsync` has to upload the entire site. Future deployments should be much faster,
as `ftpsync` will upload only new and changed files.

### Example workflow

This is a complete example how to build and deploy using Hugo and `ftpsync`.

```yaml
name: Build

on:
  push:
    branches: [ master ]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - name: Checkout
      uses: actions/checkout@v2
      with:
        submodules: true
        fetch-depth: 0

    - name: Setup Hugo
      uses: peaceiris/actions-hugo@v2
      with:
        hugo-version: '0.74.2'

    - name: Build
      run: hugo --minify
      env:
        HUGO_ENV: production

    - name: Deploy
      run: ftpsync/ftpsync.py example.cpm --user myself --password "${{ secrets.FTP_PASSWORD }}"
```
