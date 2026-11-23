using System.Runtime.Versioning;
using System.Text.Json;

namespace Yuki.WindowsCredentialBridge;

internal static class Program
{
    private const int MaxRequestBytes = 32 * 1024;

    [SupportedOSPlatform("windows")]
    private static int Main()
    {
        try
        {
            using var input = new MemoryStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = Console.OpenStandardInput().Read(buffer, 0, buffer.Length)) > 0)
            {
                if (input.Length + read > MaxRequestBytes)
                {
                    WriteError("request_too_large");
                    return 2;
                }
                input.Write(buffer, 0, read);
            }

            using JsonDocument document = JsonDocument.Parse(input.ToArray(),
                new JsonDocumentOptions { MaxDepth = 8 });
            JsonElement root = document.RootElement;
            string operation = RequiredString(root, "operation");
            string associatedData = RequiredString(root, "associated_data");
            byte[] aad = Convert.FromBase64String(associatedData);
            if (aad.Length > 4096)
            {
                WriteError("invalid_request");
                return 2;
            }

            var cipher = new WindowsCredentialCipher();
            if (operation == "encrypt")
            {
                byte[] plaintext = Convert.FromBase64String(RequiredString(root, "plaintext"));
                try
                {
                    var encrypted = cipher.Encrypt(plaintext, aad);
                    WriteJson(new
                    {
                        key_id = encrypted.KeyId,
                        ciphertext = Convert.ToBase64String(encrypted.Ciphertext)
                    });
                    return 0;
                }
                finally
                {
                    System.Security.Cryptography.CryptographicOperations.ZeroMemory(plaintext);
                    System.Security.Cryptography.CryptographicOperations.ZeroMemory(aad);
                }
            }

            if (operation == "decrypt")
            {
                string keyId = RequiredString(root, "key_id");
                byte[] ciphertext = Convert.FromBase64String(RequiredString(root, "ciphertext"));
                try
                {
                    byte[] plaintext = cipher.Decrypt(keyId, ciphertext, aad);
                    try
                    {
                        WriteJson(new { plaintext = Convert.ToBase64String(plaintext) });
                        return 0;
                    }
                    finally
                    {
                        System.Security.Cryptography.CryptographicOperations.ZeroMemory(plaintext);
                    }
                }
                finally
                {
                    System.Security.Cryptography.CryptographicOperations.ZeroMemory(ciphertext);
                    System.Security.Cryptography.CryptographicOperations.ZeroMemory(aad);
                }
            }

            WriteError("unsupported_operation");
            return 2;
        }
        catch (Exception)
        {
            // Never serialize exception text: native errors can disclose sensitive context.
            WriteError("credential_operation_failed");
            return 1;
        }
    }

    private static string RequiredString(JsonElement root, string property)
    {
        if (!root.TryGetProperty(property, out JsonElement value)
            || value.ValueKind != JsonValueKind.String)
        {
            throw new InvalidDataException("invalid request");
        }
        string result = value.GetString() ?? throw new InvalidDataException("invalid request");
        if (result.Length > 24_000)
        {
            throw new InvalidDataException("invalid request");
        }
        return result;
    }

    private static void WriteError(string code) => WriteJson(new { error = code });

    private static void WriteJson<T>(T value)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(value);
        using Stream output = Console.OpenStandardOutput();
        output.Write(bytes);
        output.WriteByte((byte)'\n');
        output.Flush();
        System.Security.Cryptography.CryptographicOperations.ZeroMemory(bytes);
    }
}
