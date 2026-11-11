import { Config } from "@remotion/cli/config";

// Same settings as the shotcraft workbench export, so a job render and an editor export match.
Config.setVideoImageFormat("jpeg");
Config.setChromiumOpenGlRenderer("angle");
Config.setConcurrency(4);
Config.setOverwriteOutput(true);
