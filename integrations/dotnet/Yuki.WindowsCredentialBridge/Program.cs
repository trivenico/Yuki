using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text.Json;

namespace Yuki.WindowsCredentialBridge;

internal static class Program
{
    private const int MaxRequestBytes = 32 * 1024;

    [SupportedOSPlatform("windows")]
    private static int Main()
    {
        using var input = new MemoryStream();
        byte[] buffer = new byte[4096];
        byte[]? requestBytes = null;
        try
        {
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

            requestBytes = input.ToArray();
            using JsonDocument document = JsonDocument.Parse(requestBytes,
                new JsonDocumentOptions { MaxDepth = 8 });
            return Execute(document.RootElement);
        }
        catch (Exception)
        {
            // Native and parser exception text can disclose sensitive context.
            WriteError("credential_operation_failed");
            return 1;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(buffer);
            if (requestBytes is not null)
            {
                CryptographicOperations.ZeroMemory(requestBytes);
            }
            if (input.TryGetBuffer(out ArraySegment<byte> backing) && backing.Array is not null)
            {
                CryptographicOperations.ZeroMemory(backing.Array);
            }
        }
    }

    [SupportedOSPlatform("windows")]
    private static int Execute(JsonElement root)
    {
        string operation = RequiredString(root, "operation");
        byte[] aad = Convert.FromBase64String(RequiredString(root, "associated_data"));
        if (aad.Length > 4096)
        {
            CryptographicOperations.ZeroMemory(aad);
            WriteError("invalid_request");
            return 2;
        }

        try
        {
            var cipher = new WindowsCredentialCipher();
            if (operation == "encrypt")
            {
                byte[] plaintext = Convert.FromBase64String(RequiredString(root, "plaintext"));
                try
                {
                    var encrypted = cipher.Encrypt(plaintext, aad);
                    try
                    {
                        WriteJson(new
                        {
                            key_id = encrypted.KeyId,
                            ciphertext = Convert.ToBase64String(encrypted.Ciphertext)
                        });
                        return 0;
                    }
                    finally
                    {
                        CryptographicOperations.ZeroMemory(encrypted.Ciphertext);
                    }
                }
                finally
                {
                    CryptographicOperations.ZeroMemory(plaintext);
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
                        CryptographicOperations.ZeroMemory(plaintext);
                    }
                }
                finally
                {
                    CryptographicOperations.ZeroMemory(ciphertext);
                }
            }

            WriteError("unsupported_operation");
            return 2;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(aad);
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
        try
        {
            using Stream output = Console.OpenStandardOutput();
            output.Write(bytes);
            output.WriteByte((byte)'\n');
            output.Flush();
        }
        finally
        {
            CryptographicOperations.ZeroMemory(bytes);
        }
    }
}
