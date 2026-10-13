"""Shell 后台操作符检测。"""

import pytest

from lumi.agents.tools.shell_syntax import has_background_operator


class TestHasBackgroundOperator:
    @pytest.mark.parametrize(
        "command",
        [
            "sleep 5 &",
            "cmd & echo $!",
            "& cmd",
            "nohup server & disown",
            # 用户真实案例形态：& 在命令中部、后跟换行
            'cd /workspace && cat /tmp/task.md | claude -p "实现任务" '
            '--permission-mode auto 2>&1 &\necho "Background PID: $!"',
            # 回归：$((1<<3)) 不能吞掉后续行（曾被误认为 heredoc）
            "echo $((1<<3))\nnohup server &",
            "echo $((5 & 3)) &",
        ],
    )
    def test_background_operator_detected(self, command):
        assert has_background_operator(command)

    @pytest.mark.parametrize(
        "command",
        [
            "a && b",
            "ls 2>&1",
            "cmd &>out",
            "cmd &>>log",
            'echo "a & b"',
            "echo 'a & b'",
            'curl "http://x?a=1&b=2"',
            r"echo a \& b",
            "cat <&0",
            "exec 3>&-",
            'grep x <<< "a&b"',
            "make |& tee build.log",
            "case $x in a) echo 1 ;;& b) echo 2 ;; esac",
            "echo $((5 & 3))",
            "echo $((1<<3))",
            "echo $(( (1+2) & 3 ))",
            "",
            "   ",
        ],
    )
    def test_legitimate_ampersand_not_flagged(self, command):
        assert not has_background_operator(command)

    def test_heredoc_body_ampersand_not_flagged(self):
        assert not has_background_operator("cat <<EOF > f.txt\nfoo & bar\nEOF")

    def test_heredoc_quoted_delimiter(self):
        assert not has_background_operator("cat <<'END'\na & b\nEND")

    def test_heredoc_dash_indented_terminator(self):
        assert not has_background_operator("cat <<-EOF\n\tx & y\n\tEOF")

    def test_ampersand_after_heredoc_terminator_detected(self):
        assert has_background_operator("cat <<EOF\nbody\nEOF\nsleep 5 &")

    def test_backgrounded_heredoc_command_detected(self):
        """heredoc 所在行剩余部分仍是命令：& 在定界词之后同样命中"""
        assert has_background_operator("cat <<EOF &\nbody\nEOF")
