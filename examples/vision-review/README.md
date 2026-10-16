# Vision review

Cursor builds a screen. A local vision model compares it with a mockup. Paths below are placeholders.

1. Set up the board, connect Cursor and Ollama, and register a vision model:

   ```bash
   agentcoop setup --connect cursor --yes
   agentcoop connect ollama --model llava --as ollama_llava --yes
   ```

   Another vision model is fine. Name the agent from the model, for example `ollama_minicpm_v`.

2. Add the project. Cursor is the driver. The vision model only checks pictures:

   ```bash
   agentcoop project add /Users/you/projects/frontend --name frontend --driver cursor --helper ollama_llava="checks screenshots against mockups" --yes
   ```

3. Start the runner and leave it running:

   ```bash
   agentcoop runner --as ollama_llava
   ```

4. In Cursor, build a settings panel, save a screenshot, and ask Cursor to have the vision helper compare it with the mockup. Cursor sends a message with both image paths attached.

5. The runner replies with fixes. On Cursor's next turn it reads the reply and applies the changes it agrees with. It does not have to apply every line.

6. Read the exchange:

   ```bash
   agentcoop log --project frontend
   agentcoop ui
   ```

`agentcoop ui` is a page for you. The agents do not use it.
