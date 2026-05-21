# snapchat-export-tool

If you ever request a copy of all your saved Snaps (Memories) from Snapchat using the My Data export feature, you may
try importing them into your gallery app and notice that they are not sorted correctly. That is because the exported
files don't contain proper metadata: instead, all the locations and timespans are located inside a separate file named
`memories_history.json`. This tool parses that file and applies the right tags to all your media, including timestamps
converted to the right timezones based on GPS coordinates.

> [!NOTE]
> Unfortunately, the JSON file, as currently provided by Snapchat, does not contain IDs, therefore it's impossible to
> map every metadata entry to its corresponding media file with 100% certainty. Files are therefore matched based on
> other criteria. This works fine in the vast majority of cases. In genuinely uncertain cases,
> correct timestamps will still be applied but locations and local timezones won't be.

## Usage

### Requirements

This tool needs the following to run:

- Python 3.11 or newer
- [ExifTool](https://exiftool.org/install.html)

If you're going to follow the installation guide, you don't need to install Python yourself. After installing ExifTool, make sure it's in your PATH.

### Installation

If you are new to Python, I recommend [installing uv](https://docs.astral.sh/uv/getting-started/installation/):

- **macOS and Linux**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows**: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

You can then use uv to install this package like so:

```shell
uv tool install git+https://github.com/drowinski/snapchat-export-tool
```

This will also take care of installing the right version of Python for you. The `snapchatexporttool` command should now
be available from anywhere.

### Requesting your Memories data from Snapchat

1. Go to [Snapchat's My Data page](https://accounts.snapchat.com/v2/download-my-data).
2. Toggle on **Export your Memories** and **Export JSON Files**, and click **Next**.
3. Select a specific date range if you want to, confirm your email, and click **Submit**.
4. Once your export is ready, Snapchat will email you the link. Download all the available ZIP files.

### Running the tool

Simply pass all the ZIP files you've received from Snapchat as arguments to the program:

```shell
cd ~/Downloads
snapchatexporttool mydata.zip mydata-2.zip
```

This will create a new directory named `snapchat_export_tool_output` in the current working directory (`~/Downloads` in
the above example) - that's where you
will find all the processed files. Alternatively, you can define your own output directory:

```shell
snapchatexporttool mydata.zip mydata-2.zip --output ~/Pictures/mysnaps
```

> [!IMPORTANT]
> Snapchat may split your export across multiple ZIP files. Include all of them in one command so the tool can find all
> your memories and their metadata.

## Disclaimer

> [!IMPORTANT]
> This software is not affiliated with, connected to, or endorsed by Snapchat or Snap Inc. Snapchat and all related
> trademarks are the property of their respective owners.

