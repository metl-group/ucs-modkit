using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BepInEx;
using BepInEx.Logging;
using HarmonyLib;

namespace Ucs.AddressablesOverlayLoader;

[BepInPlugin(PluginGuid, PluginName, PluginVersion)]
public sealed class BundleOverlayLoaderPlugin : BaseUnityPlugin
{
    public const string PluginGuid = "de.usedcarssim.modloader.bundleoverlay";
    public const string PluginName = "UCS Bundle Overlay Loader";
    public const string PluginVersion = "1.1.0";

    private static ManualLogSource _log;
    private static OverlayResolver _resolver;

    private Harmony _harmony;
    private readonly List<MethodBase> _patchedMethods = new List<MethodBase>();
    private RuntimeAssetsOverlaySession _assetsSession;

    private void Awake()
    {
        _log = Logger;
        _resolver = new OverlayResolver(Paths.GameRootPath, Logger);
        _resolver.Load();
        _assetsSession = new RuntimeAssetsOverlaySession(Paths.GameRootPath, Logger);
        _assetsSession.Apply(_resolver.GetAssetFileMappings());

        _harmony = new Harmony(PluginGuid);
        PatchAssetBundleLoadMethods();
    }

    private void OnDestroy()
    {
        try
        {
            _assetsSession?.Dispose();
            if (_harmony != null)
            {
                _harmony.UnpatchSelf();
            }
        }
        catch (Exception ex)
        {
            Logger.LogError($"Unpatch failed: {ex}");
        }
    }

    private void PatchAssetBundleLoadMethods()
    {
        var assetBundleType = AccessTools.TypeByName("UnityEngine.AssetBundle");
        if (assetBundleType == null)
        {
            Logger.LogWarning("UnityEngine.AssetBundle type not found; loader inactive.");
            return;
        }

        var prefixMethod = AccessTools.Method(typeof(BundleOverlayLoaderPlugin), nameof(LoadPathPrefix));
        if (prefixMethod == null)
        {
            Logger.LogError("Prefix method not found; loader inactive.");
            return;
        }

        var prefix = new HarmonyMethod(prefixMethod);

        var methods = assetBundleType
            .GetMethods(BindingFlags.Public | BindingFlags.Static)
            .Where(m => m.Name == "LoadFromFile" || m.Name == "LoadFromFileAsync")
            .Where(m =>
            {
                var p = m.GetParameters();
                return p.Length >= 1 && p[0].ParameterType == typeof(string);
            })
            .ToList();

        foreach (var method in methods)
        {
            _harmony.Patch(method, prefix: prefix);
            _patchedMethods.Add(method);
            Logger.LogDebug($"Patched {method.DeclaringType?.FullName}.{method.Name}({string.Join(", ", method.GetParameters().Select(x => x.ParameterType.Name))})");
        }

        Logger.LogInfo($"Bundle overlay active. Patched methods: {_patchedMethods.Count}");
    }

    private static void LoadPathPrefix(ref string path)
    {
        if (string.IsNullOrEmpty(path) || _resolver == null)
        {
            return;
        }

        try
        {
            var replacement = _resolver.Resolve(path);
            if (!string.IsNullOrEmpty(replacement) &&
                !string.Equals(path, replacement, StringComparison.OrdinalIgnoreCase))
            {
                _log?.LogInfo($"Bundle override: '{path}' -> '{replacement}'");
                path = replacement;
            }
        }
        catch (Exception ex)
        {
            _log?.LogError($"Failed to resolve bundle override for '{path}': {ex}");
        }
    }
}

internal sealed class OverlayResolver
{
    private readonly string _gameRoot;
    private readonly string _modsRoot;
    private readonly ManualLogSource _log;
    private readonly TimeSpan _reloadInterval = TimeSpan.FromSeconds(5);

    private DateTime _lastLoadUtc = DateTime.MinValue;
    private Dictionary<string, OverrideEntry> _map = new Dictionary<string, OverrideEntry>(StringComparer.OrdinalIgnoreCase);
    private Dictionary<string, OverrideEntry> _assetMap = new Dictionary<string, OverrideEntry>(StringComparer.OrdinalIgnoreCase);

    public OverlayResolver(string gameRoot, ManualLogSource log)
    {
        _gameRoot = Path.GetFullPath(gameRoot);
        _modsRoot = Path.Combine(_gameRoot, "Mods");
        _log = log;
    }

    public void Load()
    {
        _map = BuildMap(out var assetMap);
        _assetMap = assetMap;
        _lastLoadUtc = DateTime.UtcNow;
        _log.LogInfo($"Loaded {_map.Count} override entries from Mods.");
    }

    public IReadOnlyList<OverlayMapping> GetAssetFileMappings()
    {
        return _assetMap.Values
            .Where(e => e.OriginalRelativePath.EndsWith(".assets", StringComparison.OrdinalIgnoreCase))
            .Select(e => new OverlayMapping
            {
                OriginalRelativePath = e.OriginalRelativePath,
                OverrideAbsolutePath = e.OverrideAbsolutePath,
                Priority = e.Priority,
                ModDirectory = e.ModDirectory
            })
            .OrderBy(e => e.OriginalRelativePath, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    public string Resolve(string originalPath)
    {
        if (DateTime.UtcNow - _lastLoadUtc > _reloadInterval)
        {
            Load();
        }

        var full = NormalizeAbsolute(originalPath);
        if (full == null)
        {
            return null;
        }

        if (!TryGetRelative(full, _gameRoot, out var rel))
        {
            return null;
        }

        var relKey = NormalizeRelative(rel);
        if (_map.TryGetValue(relKey, out var entry) && File.Exists(entry.OverrideAbsolutePath))
        {
            return entry.OverrideAbsolutePath;
        }

        var basenameKey = "basename:" + Path.GetFileName(full).ToLowerInvariant();
        if (_map.TryGetValue(basenameKey, out entry) && File.Exists(entry.OverrideAbsolutePath))
        {
            return entry.OverrideAbsolutePath;
        }

        return null;
    }

    private Dictionary<string, OverrideEntry> BuildMap(out Dictionary<string, OverrideEntry> assetMap)
    {
        var result = new Dictionary<string, OverrideEntry>(StringComparer.OrdinalIgnoreCase);
        var assetResult = new Dictionary<string, OverrideEntry>(StringComparer.OrdinalIgnoreCase);
        if (!Directory.Exists(_modsRoot))
        {
            assetMap = assetResult;
            return result;
        }

        var modDirs = Directory.GetDirectories(_modsRoot)
            .Where(d => !Path.GetFileName(d).StartsWith("."))
            .OrderBy(d => d, StringComparer.OrdinalIgnoreCase);

        foreach (var modDir in modDirs)
        {
            try
            {
                LoadSingleMod(modDir, result, assetResult);
            }
            catch (Exception ex)
            {
                _log.LogWarning($"Skipping mod folder '{modDir}': {ex.Message}");
            }
        }

        assetMap = assetResult;
        return result;
    }

    private void LoadSingleMod(
        string modDir,
        Dictionary<string, OverrideEntry> map,
        Dictionary<string, OverrideEntry> assetMap)
    {
        var iniPath = Path.Combine(modDir, "mod.ini");
        if (!File.Exists(iniPath))
        {
            return;
        }

        var ini = ParseIni(iniPath);
        var enabled = ParseBool(ini.TryGetValue("enabled", out var en) ? en : "true", true);
        if (!enabled)
        {
            return;
        }

        var priority = ParseInt(ini.TryGetValue("priority", out var prio) ? prio : "0", 0);
        var mapFile = ini.TryGetValue("map", out var mf) ? mf : "overrides.map";
        var mapPath = Path.GetFullPath(Path.Combine(modDir, mapFile));
        if (!File.Exists(mapPath))
        {
            _log.LogWarning($"Map file missing for mod '{Path.GetFileName(modDir)}': {mapPath}");
            return;
        }

        foreach (var rawLine in File.ReadAllLines(mapPath))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith("#"))
            {
                continue;
            }

            var parts = line.Split(new[] { '|' }, 2);
            if (parts.Length != 2)
            {
                continue;
            }

            var originalRelRaw = NormalizeRelativeKeepCase(parts[0]);
            var originalRel = NormalizeRelative(parts[0]);
            var overrideRel = parts[1].Trim();
            if (overrideRel.Length == 0)
            {
                continue;
            }

            var overrideAbs = Path.GetFullPath(Path.Combine(modDir, overrideRel));
            if (!File.Exists(overrideAbs))
            {
                continue;
            }

            Register(map, originalRel, originalRelRaw, overrideAbs, priority, modDir);
            Register(assetMap, originalRel, originalRelRaw, overrideAbs, priority, modDir);

            var basenameKey = "basename:" + Path.GetFileName(originalRel).ToLowerInvariant();
            Register(map, basenameKey, originalRelRaw, overrideAbs, priority, modDir);
        }
    }

    private static void Register(
        IDictionary<string, OverrideEntry> map,
        string key,
        string originalRelRaw,
        string overrideAbs,
        int priority,
        string modDir)
    {
        if (map.TryGetValue(key, out var existing))
        {
            if (priority < existing.Priority)
            {
                return;
            }
        }

        map[key] = new OverrideEntry
        {
            OriginalRelativePath = originalRelRaw,
            OverrideAbsolutePath = overrideAbs,
            Priority = priority,
            ModDirectory = modDir
        };
    }

    private static Dictionary<string, string> ParseIni(string path)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith("#") || line.StartsWith(";"))
            {
                continue;
            }

            var idx = line.IndexOf('=');
            if (idx <= 0)
            {
                continue;
            }

            var key = line.Substring(0, idx).Trim();
            var val = line.Substring(idx + 1).Trim();
            result[key] = val;
        }

        return result;
    }

    private static bool ParseBool(string value, bool fallback)
    {
        if (bool.TryParse(value, out var b))
        {
            return b;
        }

        if (value == "1")
        {
            return true;
        }

        if (value == "0")
        {
            return false;
        }

        return fallback;
    }

    private static int ParseInt(string value, int fallback)
    {
        return int.TryParse(value, out var i) ? i : fallback;
    }

    private static string NormalizeRelative(string path)
    {
        var normalized = path.Replace('\\', '/').Trim();
        while (normalized.StartsWith("./", StringComparison.Ordinal))
        {
            normalized = normalized.Substring(2);
        }

        return normalized.ToLowerInvariant();
    }

    private static string NormalizeRelativeKeepCase(string path)
    {
        var normalized = path.Replace('\\', '/').Trim();
        while (normalized.StartsWith("./", StringComparison.Ordinal))
        {
            normalized = normalized.Substring(2);
        }

        return normalized;
    }

    private static string NormalizeAbsolute(string path)
    {
        try
        {
            return Path.GetFullPath(path);
        }
        catch
        {
            return null;
        }
    }

    private static bool TryGetRelative(string fullPath, string root, out string rel)
    {
        var f = NormalizeAbsolute(fullPath);
        var r = NormalizeAbsolute(root);
        if (f == null || r == null)
        {
            rel = string.Empty;
            return false;
        }

        if (!f.StartsWith(r, StringComparison.OrdinalIgnoreCase))
        {
            rel = string.Empty;
            return false;
        }

        rel = f.Substring(r.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return true;
    }

    private sealed class OverrideEntry
    {
        public string OriginalRelativePath;
        public string OverrideAbsolutePath;
        public int Priority;
        public string ModDirectory;
    }
}

internal sealed class OverlayMapping
{
    public string OriginalRelativePath;
    public string OverrideAbsolutePath;
    public int Priority;
    public string ModDirectory;
}

internal sealed class RuntimeAssetsOverlaySession : IDisposable
{
    private readonly string _gameRoot;
    private readonly string _backupRoot;
    private readonly ManualLogSource _log;

    public RuntimeAssetsOverlaySession(string gameRoot, ManualLogSource log)
    {
        _gameRoot = Path.GetFullPath(gameRoot);
        _backupRoot = Path.Combine(_gameRoot, "Mods", ".runtime_assets_backup");
        _log = log;
        AppDomain.CurrentDomain.ProcessExit += OnProcessExit;
    }

    public void Apply(IReadOnlyList<OverlayMapping> mappings)
    {
        RestoreIfStale();
        if (mappings == null || mappings.Count == 0)
        {
            return;
        }

        var applied = 0;
        foreach (var mapping in mappings)
        {
            if (mapping == null || string.IsNullOrWhiteSpace(mapping.OriginalRelativePath))
            {
                continue;
            }

            if (!mapping.OriginalRelativePath.EndsWith(".assets", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var targetPath = BuildAbsoluteGamePath(mapping.OriginalRelativePath);
            if (!File.Exists(targetPath))
            {
                _log.LogWarning($"[assets] Original .assets file missing: {targetPath}");
                continue;
            }

            if (string.IsNullOrWhiteSpace(mapping.OverrideAbsolutePath) || !File.Exists(mapping.OverrideAbsolutePath))
            {
                _log.LogWarning($"[assets] Override .assets file missing: {mapping.OverrideAbsolutePath}");
                continue;
            }

            var backupPath = Path.Combine(_backupRoot, mapping.OriginalRelativePath.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(backupPath))
            {
                CopyWithSidecars(targetPath, backupPath);
            }

            CopyWithSidecars(mapping.OverrideAbsolutePath, targetPath);
            applied += 1;
            _log.LogInfo($"[assets] Overlay applied: {mapping.OriginalRelativePath}");
        }

        if (applied > 0)
        {
            _log.LogInfo($"[assets] Applied {applied} .assets overlays for this session.");
        }
    }

    public void Dispose()
    {
        Restore();
        AppDomain.CurrentDomain.ProcessExit -= OnProcessExit;
    }

    private void OnProcessExit(object sender, EventArgs e)
    {
        Restore();
    }

    private void RestoreIfStale()
    {
        if (!Directory.Exists(_backupRoot))
        {
            return;
        }

        var files = Directory.EnumerateFiles(_backupRoot, "*", SearchOption.AllDirectories).ToList();
        if (files.Count == 0)
        {
            Directory.Delete(_backupRoot, true);
            return;
        }

        _log.LogWarning("[assets] Found stale runtime backup from previous session. Restoring first.");
        Restore();
    }

    private void Restore()
    {
        if (!Directory.Exists(_backupRoot))
        {
            return;
        }

        var files = Directory.EnumerateFiles(_backupRoot, "*", SearchOption.AllDirectories).ToList();
        foreach (var backupFile in files)
        {
            var rel = backupFile.Substring(_backupRoot.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (string.IsNullOrWhiteSpace(rel))
            {
                continue;
            }

            var target = Path.Combine(_gameRoot, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(target) ?? _gameRoot);
            File.Copy(backupFile, target, true);
        }

        try
        {
            Directory.Delete(_backupRoot, true);
        }
        catch (Exception ex)
        {
            _log.LogWarning($"[assets] Failed to remove backup directory: {ex.Message}");
        }

        _log.LogInfo($"[assets] Restored {files.Count} files from runtime .assets backup.");
    }

    private string BuildAbsoluteGamePath(string relativePath)
    {
        var rel = relativePath.Replace('/', Path.DirectorySeparatorChar);
        return Path.GetFullPath(Path.Combine(_gameRoot, rel));
    }

    private static void CopyWithSidecars(string source, string target)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(target) ?? ".");
        File.Copy(source, target, true);

        if (!source.EndsWith(".assets", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        foreach (var suffix in new[] { ".resS", ".resource" })
        {
            var srcSide = source + suffix;
            var dstSide = target + suffix;
            if (File.Exists(srcSide))
            {
                Directory.CreateDirectory(Path.GetDirectoryName(dstSide) ?? ".");
                File.Copy(srcSide, dstSide, true);
            }
            else if (File.Exists(dstSide))
            {
                File.Delete(dstSide);
            }
        }
    }
}
