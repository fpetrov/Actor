using System;
using System.Threading;
using System.Threading.Tasks;
using Actor.Protocol.Core.V1;
using Actor.Sdk;
using Cysharp.Net.Http;
using global::Grpc.Core;
using global::Grpc.Net.Client;

namespace Actor.Transport.Grpc
{
    // Only local transport concerns belong here. No Unity objects or game rules.
    public sealed class GrpcActorTransport : IActorTransport
    {
        private static int workersConfigured;
        private readonly object gate = new object();
        private YetAnotherHttpHandler handler;
        private GrpcChannel channel;
        private AsyncDuplexStreamingCall<GameToRuntime, RuntimeToGame> stream;
        private CancellationTokenSource lifetime;
        private Task cleanup = Task.CompletedTask;

        public Task ConnectAsync(BridgeEndpoint endpoint, CancellationToken cancellation)
        {
            if (endpoint == null) throw new ArgumentNullException(nameof(endpoint));
            cancellation.ThrowIfCancellationRequested();
            lock (gate)
            {
                if (stream != null) throw new InvalidOperationException("Transport is already connected.");
                if (Interlocked.CompareExchange(ref workersConfigured, 1, 0) == 0)
                    YetAnotherHttpHandler.SetWorkerThreads(1);
                lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
                handler = new YetAnotherHttpHandler
                {
                    Http2Only = true,
                    ConnectTimeout = TimeSpan.FromSeconds(3),
                    Http2KeepAliveInterval = TimeSpan.FromSeconds(10),
                    Http2KeepAliveTimeout = TimeSpan.FromSeconds(3)
                };
                channel = GrpcChannel.ForAddress(endpoint.Address, new GrpcChannelOptions
                {
                    HttpHandler = handler,
                    MaxReceiveMessageSize = ActorProtocol.MaxMessageBytes,
                    MaxSendMessageSize = ActorProtocol.MaxMessageBytes
                });
                var client = new ActorBridge.ActorBridgeClient(channel);
                stream = client.Connect(new Metadata { { "x-actor-bridge-token", endpoint.Token } },
                    cancellationToken: lifetime.Token);
            }
            return Task.CompletedTask;
        }

        public async Task SendAsync(GameToRuntime message, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            var current = Current();
            try { await current.RequestStream.WriteAsync(message).ConfigureAwait(false); }
            catch (RpcException exception) { throw Translate(exception, cancellation); }
        }

        public async Task<RuntimeToGame> ReceiveAsync(CancellationToken cancellation)
        {
            var current = Current();
            try
            {
                return await current.ResponseStream.MoveNext(cancellation).ConfigureAwait(false)
                    ? current.ResponseStream.Current : null;
            }
            catch (RpcException exception) { throw Translate(exception, cancellation); }
        }

        public async Task DisconnectAsync(CancellationToken cancellation)
        {
            AsyncDuplexStreamingCall<GameToRuntime, RuntimeToGame> current;
            lock (gate) current = stream;
            try
            {
                if (current != null && !cancellation.IsCancellationRequested)
                {
                    Task complete = current.RequestStream.CompleteAsync();
                    if (await Task.WhenAny(complete, Task.Delay(500, cancellation)).ConfigureAwait(false) == complete)
                        await complete.ConfigureAwait(false);
                }
            }
            catch (RpcException) { }
            catch (OperationCanceledException) { }
            catch (ObjectDisposedException) { }
            finally
            {
                Abort();
                Task pending;
                lock (gate) pending = cleanup;
                await Task.WhenAny(pending, Task.Delay(500)).ConfigureAwait(false);
            }
        }

        private AsyncDuplexStreamingCall<GameToRuntime, RuntimeToGame> Current()
        {
            lock (gate) return stream ?? throw new ActorTransportException("TransportClosed", "The local bridge stream is closed.");
        }

        private static Exception Translate(RpcException exception, CancellationToken cancellation)
        {
            if (cancellation.IsCancellationRequested || exception.StatusCode == StatusCode.Cancelled)
                return new OperationCanceledException("The local bridge operation was cancelled.", exception, cancellation);
            if (exception.StatusCode == StatusCode.Unauthenticated || exception.StatusCode == StatusCode.PermissionDenied)
                return new ActorTransportException("BridgeAuthenticationFailed", "Runtime rejected the bridge credential.", false, exception);
            if (exception.StatusCode == StatusCode.ResourceExhausted || exception.StatusCode == StatusCode.InvalidArgument ||
                exception.StatusCode == StatusCode.DataLoss || exception.StatusCode == StatusCode.Unimplemented)
                return new ActorTransportException("BridgeProtocolRejected", "Runtime rejected the bridge protocol or message limits.", false, exception);
            return new ActorTransportException("RuntimeUnavailable", "The local ACTOR Runtime is unavailable or its stream failed.", true, exception);
        }

        public void Abort()
        {
            AsyncDuplexStreamingCall<GameToRuntime, RuntimeToGame> oldStream;
            GrpcChannel oldChannel;
            YetAnotherHttpHandler oldHandler;
            CancellationTokenSource oldLifetime;
            lock (gate)
            {
                oldLifetime = lifetime; lifetime = null;
                oldStream = stream; stream = null;
                oldChannel = channel; channel = null;
                oldHandler = handler; handler = null;
            }
            // Cancellation is immediate; native worker/channel teardown never runs in a game-frame lock.
            try { oldLifetime?.Cancel(); } catch (ObjectDisposedException) { }
            oldStream?.Dispose();
            if (oldChannel == null && oldHandler == null && oldLifetime == null) return;
            Task disposal = Task.Run(() =>
            {
                try { oldChannel?.Dispose(); }
                finally { try { oldHandler?.Dispose(); } finally { oldLifetime?.Dispose(); } }
            });
            lock (gate) cleanup = Task.WhenAll(cleanup, disposal);
        }

        public void Dispose() => Abort();
    }
}
