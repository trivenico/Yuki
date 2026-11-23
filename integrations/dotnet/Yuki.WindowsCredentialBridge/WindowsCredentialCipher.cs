using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;

namespace Yuki.WindowsCredentialBridge;

[SupportedOSPlatform("windows")]
public sealed class WindowsCredentialCipher
{
    public const string CurrentKeyId = "wincred-v1";
    private const uint CredTypeGeneric = 1;
    private const uint CredPersistLocalMachine = 2;
    private const uint CryptProtectUiForbidden = 1;
    private const int KeyBytes = 32;
    private const int NonceBytes = 12;
    private const int TagBytes = 16;
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Yuki-OAuthVault-CEK-v1");
    private readonly string targetPrefix;

    public WindowsCredentialCipher(string targetPrefix = "Yuki/OAuthVault")
    {
        if (string.IsNullOrWhiteSpace(targetPrefix) || targetPrefix.Length > 180
            || targetPrefix.Any(ch => !(char.IsAsciiLetterOrDigit(ch) || ch is '/' or '-' or '_' or '.')))
        {
            throw new ArgumentException("Invalid credential target prefix.", nameof(targetPrefix));
        }
        this.targetPrefix = targetPrefix;
    }

    public (string KeyId, byte[] Ciphertext) Encrypt(ReadOnlySpan<byte> plaintext,
        ReadOnlySpan<byte> associatedData)
    {
        if (plaintext.Length is < 1 or > 8192 || associatedData.Length > 4096)
        {
            throw new ArgumentException("Credential input is outside supported bounds.");
        }

        byte[] key = GetOrCreateKey();
        try
        {
            byte[] nonce = RandomNumberGenerator.GetBytes(NonceBytes);
            byte[] ciphertext = new byte[plaintext.Length];
            byte[] tag = new byte[TagBytes];
            using (var aes = new AesGcm(key, TagBytes))
            {
                aes.Encrypt(nonce, plaintext, ciphertext, tag, associatedData);
            }

            byte[] result = new byte[NonceBytes + TagBytes + ciphertext.Length];
            nonce.CopyTo(result, 0);
            tag.CopyTo(result, NonceBytes);
            ciphertext.CopyTo(result, NonceBytes + TagBytes);
            CryptographicOperations.ZeroMemory(ciphertext);
            CryptographicOperations.ZeroMemory(tag);
            return (CurrentKeyId, result);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(key);
        }
    }

    public byte[] Decrypt(string keyId, ReadOnlySpan<byte> ciphertext,
        ReadOnlySpan<byte> associatedData)
    {
        if (!string.Equals(keyId, CurrentKeyId, StringComparison.Ordinal)
            || ciphertext.Length is < (NonceBytes + TagBytes + 1) or > 16384
            || associatedData.Length > 4096)
        {
            throw new CryptographicException("Credential decryption failed.");
        }

        byte[] key = ReadKey();
        byte[] plaintext = new byte[ciphertext.Length - NonceBytes - TagBytes];
        try
        {
            using var aes = new AesGcm(key, TagBytes);
            aes.Decrypt(ciphertext[..NonceBytes],
                ciphertext[(NonceBytes + TagBytes)..],
                ciphertext.Slice(NonceBytes, TagBytes), plaintext, associatedData);
            return plaintext;
        }
        catch (CryptographicException)
        {
            CryptographicOperations.ZeroMemory(plaintext);
            throw new CryptographicException("Credential decryption failed.");
        }
        finally
        {
            CryptographicOperations.ZeroMemory(key);
        }
    }

    public void DeleteKey()
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("Windows Credential Manager is unavailable.");
        }
        string target = TargetName(CurrentKeyId);
        if (!CredDelete(target, CredTypeGeneric, 0) && Marshal.GetLastWin32Error() != 1168)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    private byte[] GetOrCreateKey()
    {
        using var mutex = new Mutex(false, @"Local\YukiOAuthVaultKeyV1");
        bool acquired = false;
        try
        {
            try
            {
                acquired = mutex.WaitOne(TimeSpan.FromSeconds(10));
            }
            catch (AbandonedMutexException)
            {
                acquired = true;
            }
            if (!acquired)
            {
                throw new CryptographicException("Credential key operation timed out.");
            }

            try
            {
                return ReadKey();
            }
            catch (KeyNotFoundException)
            {
                byte[] key = RandomNumberGenerator.GetBytes(KeyBytes);
                try
                {
                    WriteProtectedKey(key);
                    return (byte[])key.Clone();
                }
                finally
                {
                    CryptographicOperations.ZeroMemory(key);
                }
            }
        }
        finally
        {
            if (acquired)
            {
                mutex.ReleaseMutex();
            }
        }
    }

    private byte[] ReadKey()
    {
        if (!CredRead(TargetName(CurrentKeyId), CredTypeGeneric, 0, out IntPtr credential))
        {
            int error = Marshal.GetLastWin32Error();
            if (error == 1168)
            {
                throw new KeyNotFoundException("Credential key is not provisioned.");
            }
            throw new Win32Exception(error);
        }

        try
        {
            var native = Marshal.PtrToStructure<NativeCredential>(credential);
            if (native.CredentialBlob == IntPtr.Zero || native.CredentialBlobSize is 0 or > 4096)
            {
                throw new CryptographicException("Credential key is invalid.");
            }
            byte[] protectedBytes = new byte[native.CredentialBlobSize];
            Marshal.Copy(native.CredentialBlob, protectedBytes, 0, protectedBytes.Length);
            try
            {
                byte[] key = Unprotect(protectedBytes);
                if (key.Length != KeyBytes)
                {
                    CryptographicOperations.ZeroMemory(key);
                    throw new CryptographicException("Credential key is invalid.");
                }
                return key;
            }
            finally
            {
                CryptographicOperations.ZeroMemory(protectedBytes);
            }
        }
        finally
        {
            CredFree(credential);
        }
    }

    private void WriteProtectedKey(byte[] key)
    {
        byte[] protectedBytes = Protect(key);
        IntPtr target = IntPtr.Zero;
        IntPtr user = IntPtr.Zero;
        IntPtr blob = IntPtr.Zero;
        try
        {
            target = Marshal.StringToCoTaskMemUni(TargetName(CurrentKeyId));
            user = Marshal.StringToCoTaskMemUni(Environment.UserName);
            blob = Marshal.AllocHGlobal(protectedBytes.Length);
            Marshal.Copy(protectedBytes, 0, blob, protectedBytes.Length);
            var credential = new NativeCredential
            {
                Type = CredTypeGeneric,
                TargetName = target,
                CredentialBlobSize = (uint)protectedBytes.Length,
                CredentialBlob = blob,
                Persist = CredPersistLocalMachine,
                UserName = user
            };
            if (!CredWrite(ref credential, 0))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            if (blob != IntPtr.Zero)
            {
                Marshal.WriteByte(blob, 0, 0);
                Marshal.FreeHGlobal(blob);
            }
            if (target != IntPtr.Zero) Marshal.FreeCoTaskMem(target);
            if (user != IntPtr.Zero) Marshal.FreeCoTaskMem(user);
            CryptographicOperations.ZeroMemory(protectedBytes);
        }
    }

    private static byte[] Protect(byte[] value) => TransformDpapi(value, protect: true);
    private static byte[] Unprotect(byte[] value) => TransformDpapi(value, protect: false);

    private static byte[] TransformDpapi(byte[] value, bool protect)
    {
        IntPtr inputMemory = IntPtr.Zero;
        IntPtr entropyMemory = IntPtr.Zero;
        try
        {
            inputMemory = Marshal.AllocHGlobal(value.Length);
            entropyMemory = Marshal.AllocHGlobal(Entropy.Length);
            Marshal.Copy(value, 0, inputMemory, value.Length);
            Marshal.Copy(Entropy, 0, entropyMemory, Entropy.Length);
            var input = new DataBlob { Size = value.Length, Data = inputMemory };
            var entropy = new DataBlob { Size = Entropy.Length, Data = entropyMemory };
            bool ok = protect
                ? CryptProtectData(ref input, null, ref entropy, IntPtr.Zero, IntPtr.Zero,
                    CryptProtectUiForbidden, out DataBlob output)
                : CryptUnprotectData(ref input, IntPtr.Zero, ref entropy, IntPtr.Zero,
                    IntPtr.Zero, CryptProtectUiForbidden, out output);
            if (!ok)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            try
            {
                byte[] result = new byte[output.Size];
                Marshal.Copy(output.Data, result, 0, result.Length);
                return result;
            }
            finally
            {
                for (int i = 0; i < output.Size; i++)
                {
                    Marshal.WriteByte(output.Data, i, 0);
                }
                LocalFree(output.Data);
            }
        }
        finally
        {
            if (inputMemory != IntPtr.Zero)
            {
                for (int i = 0; i < value.Length; i++) Marshal.WriteByte(inputMemory, i, 0);
                Marshal.FreeHGlobal(inputMemory);
            }
            if (entropyMemory != IntPtr.Zero) Marshal.FreeHGlobal(entropyMemory);
        }
    }

    private string TargetName(string keyId) => targetPrefix + "/" + keyId;

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob
    {
        public int Size;
        public IntPtr Data;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredential
    {
        public uint Flags;
        public uint Type;
        public IntPtr TargetName;
        public IntPtr Comment;
        public long LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public IntPtr TargetAlias;
        public IntPtr UserName;
    }

    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredWrite(ref NativeCredential credential, uint flags);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode,
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredDelete(string target, uint type, uint flags);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern void CredFree(IntPtr buffer);

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptProtectData(ref DataBlob input, string? description,
        ref DataBlob optionalEntropy, IntPtr reserved, IntPtr prompt, uint flags,
        out DataBlob output);

    [DllImport("crypt32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptUnprotectData(ref DataBlob input, IntPtr description,
        ref DataBlob optionalEntropy, IntPtr reserved, IntPtr prompt, uint flags,
        out DataBlob output);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);
}
