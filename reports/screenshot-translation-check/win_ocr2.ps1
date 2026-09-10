param(
    [string]$ImagePath,
    [string]$Lang = "zh-Hans-CN",
    [switch]$List
)

$cs = @"
using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Windows.Media.Ocr;
using Windows.Storage;
using Windows.Graphics.Imaging;

public static class WinOcr2
{
    public static async Task<string[]> RecognizeAsync(string path, string langTag)
    {
        var file = await StorageFile.GetFileFromPathAsync(path);
        using (var stream = await file.OpenAsync(FileAccessMode.Read))
        {
            var decoder = await BitmapDecoder.CreateAsync(stream);
            var bitmap = await decoder.GetSoftwareBitmapAsync();
            var lang = new Windows.Globalization.Language(langTag);
            var engine = OcrEngine.TryCreateFromLanguage(lang);
            if (engine == null) return new[] { "ERROR: engine null for " + langTag };
            var result = await engine.RecognizeAsync(bitmap);
            var lines = new List<string>();
            foreach (var line in result.Lines)
            {
                var rect = line.Words[0].BoundingRect;
                var text = "";
                foreach (var w in line.Words) { if (text.Length > 0) text += " "; text += w.Text; }
                lines.Add(String.Format("{0,5:N0} {1,5:N0} | {2}", rect.Y, rect.X, text));
            }
            return lines.ToArray();
        }
    }
}
"@

Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -TypeDefinition $cs

if ($List) {
    [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }
    exit 0
}

$task = [WinOcr2]::RecognizeAsync($ImagePath, $Lang)
$task.Wait()
$task.Result
