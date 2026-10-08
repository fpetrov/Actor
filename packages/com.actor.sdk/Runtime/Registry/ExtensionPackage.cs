using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using Actor.Protocol.Core.V1;
using Google.Protobuf;
using Google.Protobuf.Reflection;

namespace Actor.Sdk
{
    public sealed class ExtensionPackage
    {
        public string Id { get; }
        public string Version { get; }
        public string SchemaHash { get; }
        public uint SupportedProtocolMajor { get; }
        public bool Required { get; }
        public ByteString DescriptorSet { get; }
        private readonly Dictionary<string, FileDescriptorProto> descriptorFiles =
            new Dictionary<string, FileDescriptorProto>(StringComparer.Ordinal);
        private readonly HashSet<string> messageTypes = new HashSet<string>(StringComparer.Ordinal);

        public ExtensionPackage(string id, string version, string schemaHash, byte[] descriptorSet,
            uint supportedProtocolMajor = ActorProtocol.Major, bool required = true)
        {
            Id = Require.Text(id, nameof(id));
            Version = Require.Text(version, nameof(version));
            if (schemaHash == null || schemaHash.Length != 64)
                throw new ArgumentException("Schema hash must be a lowercase SHA-256 hex digest.", nameof(schemaHash));
            foreach (char c in schemaHash)
                if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f'))
                    throw new ArgumentException("Schema hash must be a lowercase SHA-256 hex digest.", nameof(schemaHash));
            SchemaHash = schemaHash;
            SupportedProtocolMajor = supportedProtocolMajor;
            Required = required;
            if (descriptorSet == null || descriptorSet.Length == 0)
                throw new ArgumentException("Extension descriptor set is required.", nameof(descriptorSet));
            DescriptorSet = ByteString.CopyFrom(descriptorSet);
            using (var digest = SHA256.Create())
            {
                string actual = BitConverter.ToString(digest.ComputeHash(descriptorSet)).Replace("-", "").ToLowerInvariant();
                if (actual != schemaHash) throw new ArgumentException("Descriptor schema hash mismatch.", nameof(descriptorSet));
            }
            var parsed = FileDescriptorSet.Parser.ParseFrom(descriptorSet);
            foreach (FileDescriptorProto file in parsed.File)
            {
                if (descriptorFiles.ContainsKey(file.Name)) throw new ArgumentException("Duplicate descriptor filename.", nameof(descriptorSet));
                descriptorFiles.Add(file.Name, file);
                foreach (DescriptorProto message in file.MessageType)
                    AddMessageTypes(string.IsNullOrEmpty(file.Package) ? "" : file.Package + ".", message);
            }
            if (descriptorFiles.Count == 0) throw new ArgumentException("Descriptor set contains no files.", nameof(descriptorSet));
        }

        private void AddMessageTypes(string prefix, DescriptorProto message)
        {
            string name = prefix + message.Name;
            if (!messageTypes.Add(name)) throw new ArgumentException("Duplicate descriptor message: " + name);
            foreach (DescriptorProto nested in message.NestedType) AddMessageTypes(name + ".", nested);
        }

        public bool ContainsMessage<T>() where T : IMessage<T>, new()
        {
            MessageDescriptor descriptor = ((IMessage)new T()).Descriptor;
            if (!messageTypes.Contains(descriptor.FullName) || !descriptorFiles.TryGetValue(descriptor.File.Name, out FileDescriptorProto packaged))
                return false;
            // Full name alone cannot prove compiled bindings match the advertised schema.
            FileDescriptorProto compiled = descriptor.File.ToProto();
            compiled.SourceCodeInfo = null;
            var canonical = packaged.Clone();
            canonical.SourceCodeInfo = null;
            // Generated C# descriptors omit derived JSON names; descriptor_set_out includes them.
            NormalizeJsonNames(compiled);
            NormalizeJsonNames(canonical);
            return compiled.Equals(canonical);
        }

        private static void NormalizeJsonNames(FileDescriptorProto file)
        {
            foreach (FieldDescriptorProto extension in file.Extension) NormalizeJsonName(extension);
            foreach (DescriptorProto message in file.MessageType) NormalizeJsonNames(message);
        }

        private static void NormalizeJsonNames(DescriptorProto message)
        {
            foreach (FieldDescriptorProto field in message.Field) NormalizeJsonName(field);
            foreach (FieldDescriptorProto extension in message.Extension) NormalizeJsonName(extension);
            foreach (DescriptorProto nested in message.NestedType) NormalizeJsonNames(nested);
        }

        private static void NormalizeJsonName(FieldDescriptorProto field)
        {
            if (field.HasJsonName) return;
            var result = new StringBuilder();
            bool capitalize = false;
            foreach (char c in field.Name)
            {
                if (c == '_') { capitalize = true; continue; }
                result.Append(capitalize ? char.ToUpperInvariant(c) : c);
                capitalize = false;
            }
            field.JsonName = result.ToString();
        }

        public ExtensionManifest ToManifest() => new ExtensionManifest
        {
            ExtensionId = Id,
            ExtensionVersion = Version,
            SchemaHash = SchemaHash,
            SupportedProtocolMajor = SupportedProtocolMajor,
            Required = Required
        };
    }
}
