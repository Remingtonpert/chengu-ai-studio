// Strata 本地部署启动器：定位项目虚拟环境并把命令转发给对应入口模块。
// 用 Windows 自带的 .NET Framework csc.exe 编译，见 scripts/build_launcher.ps1。
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace StrataLauncher
{
    internal static class Program
    {
        // 按 Windows CreateProcess 的命令行规则转义单个参数。
        private static string EscapeArgument(string arg)
        {
            if (string.IsNullOrEmpty(arg))
            {
                return "\"\"";
            }
            StringBuilder sb = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\')
                {
                    backslashes++;
                    sb.Append(c);
                }
                else if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                    backslashes = 0;
                }
                else
                {
                    sb.Append(c);
                    backslashes = 0;
                }
            }
            sb.Append('\\', backslashes * 2);
            sb.Append('"');
            return sb.ToString();
        }

        private static int Run(string fileName, string arguments, string workingDirectory)
        {
            ProcessStartInfo startInfo = new ProcessStartInfo(fileName, arguments);
            startInfo.WorkingDirectory = workingDirectory;
            startInfo.UseShellExecute = false;
            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }

        private static int CheckEnvironment(string baseDir, string venvPython)
        {
            Console.WriteLine("== Strata 本地环境自检 ==");
            Console.WriteLine("项目目录 : " + baseDir);
            int failures = 0;

            Console.WriteLine("[OK]   主虚拟环境 : " + venvPython);
            if (Run(venvPython, "-V", baseDir) != 0)
            {
                failures++;
            }

            string imagePython = Path.Combine(baseDir, ".venv-image", "Scripts", "python.exe");
            if (File.Exists(imagePython))
            {
                Console.WriteLine("[OK]   引擎虚拟环境 : " + imagePython);
                if (Run(imagePython, "-V", baseDir) != 0)
                {
                    failures++;
                }
            }
            else
            {
                Console.WriteLine("[缺失] 引擎虚拟环境 .venv-image（图像/视频引擎将无法启动）");
            }

            Console.WriteLine("检查 PySide6 ...");
            int code = Run(
                venvPython,
                "-c " + EscapeArgument("import PySide6; print('[OK]   PySide6', PySide6.__version__)"),
                baseDir);
            if (code != 0)
            {
                Console.WriteLine("[失败] PySide6 无法导入，请运行: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt");
                failures++;
            }

            string[] entryFiles = new string[]
            {
                Path.Combine("app", "bootstrap.py"),
                Path.Combine("app", "web", "server.py"),
            };
            foreach (string entryFile in entryFiles)
            {
                if (File.Exists(Path.Combine(baseDir, entryFile)))
                {
                    Console.WriteLine("[OK]   入口文件 : " + entryFile);
                }
                else
                {
                    Console.WriteLine("[缺失] 入口文件 : " + entryFile);
                    failures++;
                }
            }

            Console.WriteLine(failures == 0 ? "自检通过。" : ("自检发现 " + failures + " 个问题。"));
            return failures == 0 ? 0 : 1;
        }

        private static int Main(string[] args)
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string mode = "app";
            string[] rest = new string[0];
            if (args.Length > 0)
            {
                mode = args[0].ToLowerInvariant();
                if (args.Length > 1)
                {
                    rest = new string[args.Length - 1];
                    Array.Copy(args, 1, rest, 0, rest.Length);
                }
            }

            string module;
            switch (mode)
            {
                case "app":
                    module = "app.bootstrap";
                    break;
                case "web":
                    module = "app.web.server";
                    break;
                case "check":
                    module = null;
                    break;
                default:
                    Console.Error.WriteLine("用法: Strata.exe [app|web|check] [附加参数...]");
                    Console.Error.WriteLine("  (无参数)  启动 Strata 桌面应用");
                    Console.Error.WriteLine("  web       启动本地网页控制台（默认 http://127.0.0.1:8765）");
                    Console.Error.WriteLine("  check     运行本地环境自检");
                    Console.Error.WriteLine("附加参数会原样传给对应的 Python 入口。");
                    return 2;
            }

            string venvPython = Path.Combine(baseDir, ".venv", "Scripts", "python.exe");
            if (!File.Exists(venvPython))
            {
                Console.Error.WriteLine("未找到项目虚拟环境: " + venvPython);
                Console.Error.WriteLine("请先执行: py -3.11 -m venv .venv");
                Console.Error.WriteLine("然后:     .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt");
                Console.WriteLine();
                Console.WriteLine("按 Enter 键退出...");
                try
                {
                    Console.ReadLine();
                }
                catch (Exception)
                {
                }
                return 1;
            }

            if (module == null)
            {
                return CheckEnvironment(baseDir, venvPython);
            }

            StringBuilder arguments = new StringBuilder("-m " + module);
            foreach (string extra in rest)
            {
                arguments.Append(' ').Append(EscapeArgument(extra));
            }
            return Run(venvPython, arguments.ToString(), baseDir);
        }
    }
}
