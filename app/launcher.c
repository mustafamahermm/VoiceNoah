// VoiceNoah.app launcher: starts the Python app as a child process so macOS
// asks for Microphone / Accessibility permission for "VoiceNoah" (not Terminal).
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child = 0;

static void forward(int sig) { if (child > 0) kill(child, sig); }

int main(void) {
    const char *dir = VOICENOAH_DIR;
    char py[1024], script[1024], logp[1024];
    snprintf(py, sizeof py, "%s/.venv/bin/python", dir);
    snprintf(script, sizeof script, "%s/voicenoah.py", dir);
    snprintf(logp, sizeof logp, "%s/Library/Logs/VoiceNoah.log", getenv("HOME") ? getenv("HOME") : "/tmp");

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 1, logp, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);
    posix_spawn_file_actions_addchdir_np(&fa, dir);

    setenv("PYTHONUNBUFFERED", "1", 1);
    char *argv[] = {py, script, NULL};
    if (posix_spawn(&child, py, &fa, NULL, argv, environ) != 0) {
        perror("VoiceNoah: could not start Python");
        return 1;
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
