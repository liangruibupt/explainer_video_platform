// Entry for part B's commands until A's js/cli.ts imports SHOT_COMMANDS (see commands.ts).
import { SHOT_COMMANDS } from "./commands";

const [cmd, ...rest] = process.argv.slice(2);
const fn = SHOT_COMMANDS[cmd];
if (!fn) {
  console.error(`usage: shot.mjs <${Object.keys(SHOT_COMMANDS).join("|")}> ...`);
  process.exit(64);
}
try {
  process.exit(fn(rest));
} catch (e) {
  console.error(String((e as Error)?.stack ?? e));
  process.exit(1);
}
