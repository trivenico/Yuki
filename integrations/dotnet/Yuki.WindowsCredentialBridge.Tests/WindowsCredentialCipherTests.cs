using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using Xunit;
using Yuki.WindowsCredentialBridge;

namespace Yuki.WindowsCredentialBridge.Tests;

[SupportedOSPlatform("windows")]
public sealed class WindowsCredentialCipherTests
{
    [Fact]
    public void EncryptDecryptPersistsKeyAcrossCipherInstances()
    {
        string prefix = "Yuki/OAuthVault/Tests/" + Guid.NewGuid().ToString("N");
        var first = new WindowsCredentialCipher(prefix);
        var second = new WindowsCredentialCipher(prefix);
        byte[] plaintext = Encoding.UTF8.GetBytes("test-access-token");
        byte[] aad = Encoding.UTF8.GetBytes("[1,\"owner\",\"provider\",\"account\",\"access\"]");
        try
        {
            var encrypted = first.Encrypt(plaintext, aad);
            Assert.Equal(WindowsCredentialCipher.CurrentKeyId, encrypted.KeyId);
            byte[] decrypted = second.Decrypt(encrypted.KeyId, encrypted.Ciphertext, aad);
            try
            {
                Assert.Equal(plaintext, decrypted);
            }
            finally
            {
                CryptographicOperations.ZeroMemory(decrypted);
                CryptographicOperations.ZeroMemory(encrypted.Ciphertext);
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
            CryptographicOperations.ZeroMemory(aad);
            first.DeleteKey();
        }
    }

    [Fact]
    public void AssociatedDataMismatchFailsClosed()
    {
        string prefix = "Yuki/OAuthVault/Tests/" + Guid.NewGuid().ToString("N");
        var cipher = new WindowsCredentialCipher(prefix);
        byte[] plaintext = Encoding.UTF8.GetBytes("test-refresh-token");
        byte[] aad = Encoding.UTF8.GetBytes("owner-A/provider/account/refresh");
        byte[] wrongAad = Encoding.UTF8.GetBytes("owner-B/provider/account/refresh");
        byte[]? encryptedBytes = null;
        try
        {
            var encrypted = cipher.Encrypt(plaintext, aad);
            encryptedBytes = encrypted.Ciphertext;
            Assert.Throws<CryptographicException>(() =>
                cipher.Decrypt(encrypted.KeyId, encrypted.Ciphertext, wrongAad));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
            CryptographicOperations.ZeroMemory(aad);
            CryptographicOperations.ZeroMemory(wrongAad);
            if (encryptedBytes is not null) CryptographicOperations.ZeroMemory(encryptedBytes);
            cipher.DeleteKey();
        }
    }

    [Fact]
    public void CiphertextTamperingFailsAuthentication()
    {
        string prefix = "Yuki/OAuthVault/Tests/" + Guid.NewGuid().ToString("N");
        var cipher = new WindowsCredentialCipher(prefix);
        byte[] plaintext = Encoding.UTF8.GetBytes("token-to-protect");
        byte[] aad = Encoding.UTF8.GetBytes("aad");
        try
        {
            var encrypted = cipher.Encrypt(plaintext, aad);
            encrypted.Ciphertext[^1] ^= 0x01;
            Assert.Throws<CryptographicException>(() =>
                cipher.Decrypt(encrypted.KeyId, encrypted.Ciphertext, aad));
            CryptographicOperations.ZeroMemory(encrypted.Ciphertext);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plaintext);
            CryptographicOperations.ZeroMemory(aad);
            cipher.DeleteKey();
        }
    }
}
