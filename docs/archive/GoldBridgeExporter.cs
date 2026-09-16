#region Using declarations
using System;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

// ============================================================================
//  GoldBridgeExporter  (NinjaTrader 8 indicator)
// ----------------------------------------------------------------------------
//  Streams live market data (trades, bid/ask quotes, and order-book depth)
//  from NinjaTrader to a CSV file, so the Python robot
//  (gold_robot_ntbridge.py) can read it.
//
//  WHY: your Rithmic trial account works inside NinjaTrader (NinjaTrader's
//  application is authorized by Rithmic), but custom applications are blocked
//  by Rithmic's permission policy. This bridge uses NinjaTrader's authorized
//  connection as the data source.
//
//  INSTALL:  NinjaScript Editor -> right-click "Indicators" -> New Indicator ->
//            name it GoldBridgeExporter -> Generate -> select ALL generated
//            code, delete it, paste THIS ENTIRE FILE -> press F5 to compile.
//
//  USE:      Open a chart of GC (front month, any timeframe), add the
//            "GoldBridgeExporter" indicator to it, and keep the chart open.
//            Data is written to  C:\NinjaBridge\ticks.csv
//
//  NOTES:    - File is opened/closed per write, so the file is never locked
//              by NinjaTrader and survives restarts.
//            - Numbers are written with invariant culture (dots, not commas),
//              so it works on Hungarian Windows too.
// ============================================================================

namespace NinjaTrader.NinjaScript.Indicators
{
    public class GoldBridgeExporter : Indicator
    {
        private string filePath;
        private long bytesSinceCheck = 0;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Exports live market data (trades, quotes, depth) to a CSV file for the Python robot.";
                Name = "GoldBridgeExporter";
                Calculate = Calculate.OnEachTick;      // required: receive every tick event
                IsOverlay = true;
                DrawOnPricePanel = true;
                OutputFolder = @"C:\NinjaBridge";
                ExportDepth = true;
                MaxFileMB = 250;
                KeepOldFiles = 2;
            }
            else if (State == State.Configure)
            {
                try
                {
                    Directory.CreateDirectory(OutputFolder);
                    filePath = Path.Combine(OutputFolder, "ticks.csv");
                    if (!File.Exists(filePath))
                        File.AppendAllText(filePath,
                            "time,event,price,size,level,operation,instrument" + Environment.NewLine);
                    MaybeRotate();   // also cap a file that is already oversized at startup
                    Print("GoldBridgeExporter: writing to " + filePath);
                }
                catch (Exception ex)
                {
                    filePath = null;
                    Print("GoldBridgeExporter: CANNOT create output file: " + ex.Message);
                }
            }
        }

        protected override void OnBarUpdate()
        {
            // Not used - we only care about tick-level events below.
        }

        // Level 1: every trade / best bid / best ask event
        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (filePath == null)
                return;
            // MarketDataType.ToString() gives "Last", "Bid", "Ask" (and a few
            // exotic types the Python side simply ignores).
            Write(e.Time, e.MarketDataType.ToString(), e.Price, e.Volume, -1, "", Instrument.FullName);
        }

        // Level 2: order book depth (DOM) updates - if the feed provides them
        protected override void OnMarketDepth(MarketDepthEventArgs e)
        {
            // e.IsReset events are UI-reconnect resets, not real depth data - skip them
            if (filePath == null || !ExportDepth || e.IsReset)
                return;
            // e.g. event "DepthBid" / "DepthAsk", operation "Add"/"Update"/"Remove"
            Write(e.Time, "Depth" + e.MarketDataType.ToString(), e.Price, e.Volume, e.Position,
                  e.Operation.ToString(), Instrument.FullName);
        }

        private void Write(DateTime t, string ev, double price, long size, int level, string op, string instrument)
        {
            try
            {
                // InvariantCulture -> always "4102.25", never "4102,25"
                string line = string.Format(CultureInfo.InvariantCulture,
                    "{0:yyyy-MM-ddTHH:mm:ss.fff},{1},{2:R},{3},{4},{5},{6}{7}",
                    t, ev, price, size, level, op, instrument, Environment.NewLine);
                File.AppendAllText(filePath, line);

                // check the size cap about every 25 MB written
                bytesSinceCheck += line.Length;
                if (bytesSinceCheck > 25L * 1024 * 1024)
                {
                    bytesSinceCheck = 0;
                    MaybeRotate();
                }
            }
            catch (Exception ex)
            {
                Print("GoldBridgeExporter write error: " + ex.Message);
            }
        }

        // Keeps ticks.csv from growing forever: at MaxFileMB it archives the
        // file (ticks_YYYYMMDD_HHMMSS.csv) and starts a fresh one, keeping at
        // most KeepOldFiles archives. If the Python robot currently holds the
        // file open (Windows blocks renaming then), it resets the file in
        // place instead - the cap is always enforced, archives only when possible.
        private void MaybeRotate()
        {
            if (filePath == null || MaxFileMB <= 0)
                return;
            try
            {
                FileInfo fi = new FileInfo(filePath);
                if (!fi.Exists || fi.Length < (long)MaxFileMB * 1024L * 1024L)
                    return;

                string header = "time,event,price,size,level,operation,instrument" + Environment.NewLine;
                bool archived = false;
                try
                {
                    string bak = Path.Combine(OutputFolder,
                        "ticks_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".csv");
                    File.Move(filePath, bak);
                    archived = true;
                }
                catch
                {
                    // file is held open by the Python robot - reset in place instead
                    try { File.WriteAllText(filePath, ""); }
                    catch (Exception ex2)
                    {
                        Print("GoldBridgeExporter: cannot cap data file: " + ex2.Message);
                        return;
                    }
                }

                File.AppendAllText(filePath, header);   // fresh file with header

                // delete oldest archives beyond KeepOldFiles
                try
                {
                    string[] old = Directory.GetFiles(OutputFolder, "ticks_*.csv");
                    Array.Sort(old);
                    int excess = old.Length - KeepOldFiles;
                    for (int i = 0; i < excess; i++)
                    {
                        try { File.Delete(old[i]); }
                        catch { }
                    }
                }
                catch { }

                Print(archived
                    ? "GoldBridgeExporter: data file hit the size cap - archived and started fresh"
                    : "GoldBridgeExporter: data file hit the size cap - reset (stop the Python robot at day's end to keep archives)");
            }
            catch (Exception ex)
            {
                Print("GoldBridgeExporter rotation check failed: " + ex.Message);
            }
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Output folder", GroupName = "Bridge settings", Order = 1)]
        public string OutputFolder { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Export market depth (L2)", GroupName = "Bridge settings", Order = 2)]
        public bool ExportDepth { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Max file size (MB)", GroupName = "Bridge settings", Order = 3)]
        public int MaxFileMB { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Archived files to keep", GroupName = "Bridge settings", Order = 4)]
        public int KeepOldFiles { get; set; }
        #endregion
    }
}

