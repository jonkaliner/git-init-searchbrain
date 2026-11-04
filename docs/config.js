// Settings of the website. Nothing secret belongs in this file: it is public.
window.SEARCHBRAIN_CONFIG = {
  // Shown in the greeting ("Good morning, Jon"). Leave empty for no name.
  userName: "",
  // The GitHub repository that runs the weekly update. Used for the "Run workflow" link.
  repo: "jonkaliner/git-init-searchbrain",
  // Address of the Cloudflare Worker that writes AI answers (see worker/README.md).
  // Empty = no AI answers: the Ask field then only searches the saved startups.
  workerUrl: "",
};
