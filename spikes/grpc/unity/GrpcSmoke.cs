using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Cysharp.Net.Http;
using Google.Protobuf;
using Google.Protobuf.WellKnownTypes;
using Grpc.Core;
using Grpc.Net.Client;
using UnityEngine;

public static class GrpcSmoke
{
    [Serializable] public sealed class Check { public string name; public bool passed; public string detail; }
    [Serializable] public sealed class Report
    {
        public string platform, backend;
        public bool completed;
        public Check[] checks;
        public double medianRttMs, p95RttMs;
        public int frames;
        public string error;
    }
    private static readonly Method<BytesValue, BytesValue> Method = new Method<BytesValue, BytesValue>(
        MethodType.DuplexStreaming, "actor.spike.Echo", "Connect",
        Marshallers.Create<BytesValue>(value => value.ToByteArray(), bytes => BytesValue.Parser.ParseFrom(bytes)),
        Marshallers.Create<BytesValue>(value => value.ToByteArray(), bytes => BytesValue.Parser.ParseFrom(bytes)));

    public static async Task<Report> Run(string endpoint, string token)
    {
        var checks = new List<Check>();
        var samples = new List<double>();
        var report = new Report { platform = Application.platform.ToString(), backend = "Mono" };
        try
        {
            using var handler = new YetAnotherHttpHandler { Http2Only = true, ConnectTimeout = TimeSpan.FromSeconds(3) };
            using var channel = GrpcChannel.ForAddress(endpoint, new GrpcChannelOptions { HttpHandler = handler });
            var invoker = channel.CreateCallInvoker();
            var headers = new Metadata { { "x-actor-spike-token", token } };
            for (int round = 0; round < 2; round++)
            {
                using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(15));
                using var call = invoker.AsyncDuplexStreamingCall(Method, null,
                    new CallOptions(headers, cancellationToken: cancellation.Token));
                var firstTick = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                var allEchoes = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                var sent = new System.Collections.Concurrent.ConcurrentDictionary<string, long>();
                int received = 0, ticks = 0;
                bool exact = true;
                int count = round == 0 ? 30 : 10;
                Task reader = Task.Run(async () =>
                {
                    while (await call.ResponseStream.MoveNext(cancellation.Token).ConfigureAwait(false))
                    {
                        byte[] bytes = call.ResponseStream.Current.Value.ToByteArray();
                        string prefix = bytes.Length >= 5 ? Encoding.UTF8.GetString(bytes, 0, 5) : "";
                        if (prefix == "tick:") { ++ticks; firstTick.TrySetResult(true); continue; }
                        if (prefix != "echo:") { exact = false; continue; }
                        string key = Convert.ToBase64String(bytes, 5, bytes.Length - 5);
                        if (!sent.TryRemove(key, out long timestamp)) { exact = false; continue; }
                        samples.Add((Stopwatch.GetTimestamp() - timestamp) * 1000d / Stopwatch.Frequency);
                        if (++received == count) allEchoes.TrySetResult(true);
                    }
                });
                await firstTick.Task.WaitBounded(3000).ConfigureAwait(false);
                Add(round == 0 ? "Server sends independently before client payload" : "Reconnect receives a fresh server stream", ticks > 0);
                for (int i = 0; i < count; i++)
                {
                    byte[] bytes = Encoding.UTF8.GetBytes("client-" + round + "-" + i).Concat(new byte[] { 0, 255 }).ToArray();
                    sent[Convert.ToBase64String(bytes)] = Stopwatch.GetTimestamp();
                    await call.RequestStream.WriteAsync(new BytesValue { Value = ByteString.CopyFrom(bytes) }).ConfigureAwait(false);
                    await Task.Delay(20, cancellation.Token).ConfigureAwait(false);
                }
                await allEchoes.Task.WaitBounded(3000).ConfigureAwait(false);
                Add(round == 0 ? "True bidirectional binary stream" : "Reconnect exchanges messages", exact && received == count && ticks >= 2);
                if (round == 0)
                {
                    cancellation.Cancel();
                    bool cancelled = false;
                    try { await reader.WaitBounded(2000).ConfigureAwait(false); }
                    catch (RpcException exception) { cancelled = exception.StatusCode == StatusCode.Cancelled; }
                    catch (OperationCanceledException) { cancelled = true; }
                    Add("Cancellation terminates receive task", cancelled);
                }
                else
                {
                    await call.RequestStream.CompleteAsync().ConfigureAwait(false);
                    await reader.WaitBounded(2000).ConfigureAwait(false);
                    Add("Half-close gracefully completes both directions", call.GetStatus().StatusCode == StatusCode.OK);
                }
            }
            using (var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(3)))
            using (var call = invoker.AsyncDuplexStreamingCall(Method, null,
                new CallOptions(new Metadata { { "x-actor-spike-token", "invalid-fixture-token" } }, cancellationToken: cancellation.Token)))
            {
                bool rejected = false;
                try { await call.ResponseStream.MoveNext(cancellation.Token).ConfigureAwait(false); }
                catch (RpcException exception) { rejected = exception.StatusCode == StatusCode.Unauthenticated; }
                Add("Incorrect token is rejected", rejected);
            }
            samples.Sort();
            report.medianRttMs = samples[samples.Count / 2];
            report.p95RttMs = samples[(int)(samples.Count * 0.95) - 1];
            report.completed = true;
        }
        catch (Exception exception) { report.error = exception.ToString(); }
        report.checks = checks.ToArray();
        return report;

        void Add(string name, bool passed) => checks.Add(new Check { name = name, passed = passed, detail = "" });
    }

    private static async Task WaitBounded(this Task task, int milliseconds)
    {
        if (await Task.WhenAny(task, Task.Delay(milliseconds)).ConfigureAwait(false) != task)
            throw new TimeoutException("Spike operation exceeded deadline.");
        await task.ConfigureAwait(false);
    }

    public static string Argument(string name, string fallback = null)
    {
        string[] args = Environment.GetCommandLineArgs();
        int index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : fallback;
    }
}

public sealed class GrpcSmokePlayer : MonoBehaviour
{
    private int frames;
    private bool finished;
    private string status = "Testing true gRPC bidirectional streaming…";
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap() => new GameObject("gRPC smoke fixture").AddComponent<GrpcSmokePlayer>();
    private async void Start()
    {
        Application.runInBackground = true;
        string token = File.ReadAllText(GrpcSmoke.Argument("--spike-token-file")).Trim();
        var result = await GrpcSmoke.Run(GrpcSmoke.Argument("--spike-endpoint", "http://127.0.0.1:50061"), token);
        result.frames = frames;
        File.WriteAllText(GrpcSmoke.Argument("--spike-report"), JsonUtility.ToJson(result, true));
        status = result.completed && result.checks.All(check => check.passed) ? "gRPC spike PASSED" : "gRPC spike FAILED";
        UnityEngine.Debug.Log(status);
        finished = true;
        if (GrpcSmoke.Argument("--spike-exit") == "true") Application.Quit(status.EndsWith("PASSED") ? 0 : 1);
    }
    private void Update() { if (!finished) ++frames; }
    private void OnGUI() => GUI.Label(new Rect(30, 30, 800, 60), status + " | frames=" + frames);
}
