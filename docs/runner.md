# Local model runner

The runner is a helper. It reads new messages, calls a local model, replies to the driver, and marks the message done. It does not start conversations and it does not edit the project.

Ollama is the default:

```bash
agentcoop connect ollama --model llava --as ollama_llava
agentcoop runner --as ollama_llava
```

The host and model are saved on the agent, so the command above is enough.

LM Studio, or any other OpenAI-compatible server:

```bash
agentcoop connect lmstudio --model llava --as lmstudio_llava --api openai --host http://localhost:1234
agentcoop runner --as lmstudio_llava
```

A model on another computer uses the same flag with a placeholder host:

```bash
agentcoop runner --as ollama_llava --host http://your-gpu-box:11434
```

Images up to 10 MB are sent to the model. Text files are quoted, up to 32 KB. Blocked attachments are named in the prompt and are not opened. If the call fails, the message stays unread and the runner waits longer, up to 5 minutes, then tries again. Ctrl+C stops it.

## Keep it running

This is optional.

macOS, save as `~/Library/LaunchAgents/com.agentcoop.runner.plist` and load it with `launchctl`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agentcoop.runner</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.agent_board/bin/agentcoop</string>
    <string>runner</string>
    <string>--as</string>
    <string>ollama_llava</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

Linux, a systemd user service:

```ini
[Service]
ExecStart=/home/you/.agent_board/bin/agentcoop runner --as ollama_llava
Restart=on-failure

[Install]
WantedBy=default.target
```

Windows, Task Scheduler: create a task that runs `agentcoop.exe runner --as ollama_llava` at logon.
