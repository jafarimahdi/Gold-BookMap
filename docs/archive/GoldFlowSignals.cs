#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.SuperDom;
using NinjaTrader.Gui.Tools;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.Core.FloatingPoint;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// ============================================================================
//  GoldFlowSignals  (NinjaTrader 8 indicator)
// ----------------------------------------------------------------------------
//  Draws order-flow signals LIVE on your gold chart:
//
//    GREEN up-arrow  = big trade that hit the ASK (aggressive buyer)
//    RED down-arrow  = big trade that hit the BID (aggressive seller)
//    ORANGE dot      = big trade, side unknown
//    "BURST 43"      = unusual burst: many trades within the time window
//    Blue line (sub-panel below chart) = order-book imbalance 0-100%
//           above 70 = buyers stacked   |   below 30 = sellers stacked
//
//  INSTALL (same as GoldBridgeExporter):
//    NinjaScript Editor -> right-click "Indicators" -> New Indicator ->
//    name it GoldFlowSignals -> Generate -> select ALL generated code,
//    delete it, paste THIS ENTIRE FILE -> press F5.
//
//  USE: right-click your MGC/GC chart -> Indicators... -> add GoldFlowSignals.
//       It gets its own sub-panel for the imbalance line; arrows/labels
//       appear on the price chart itself.
//
//  NOTES:
//    - REAL-TIME ONLY: NinjaTrader does not replay tick/depth data to
//      indicators on historical bars, so markers appear only while the
//      market is live. The imbalance line sits at 50% on historical bars.
//    - Defaults suit MGC (Micro gold). On full-size GC use BigTradeLots ~50.
//    - Runs happily alongside GoldBridgeExporter + the Python robot.
// ============================================================================

namespace NinjaTrader.NinjaScript.Indicators
{
    public class GoldFlowSignals : Indicator
    {
        private double bestBid = 0;
        private double bestAsk = 0;
        private Dictionary<double, long> bidBook = new Dictionary<double, long>();
        private Dictionary<double, long> askBook = new Dictionary<double, long>();
        private List<DateTime> tradeTimes = new List<DateTime>();
        private int lastBurstBar = -1;
        private int seq = 0;
        private Queue<string> drawTags = new Queue<string>();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Marks big trades and trade bursts on the chart, plus a live order-book imbalance gauge in a sub-panel.";
                Name = "GoldFlowSignals";
                Calculate = Calculate.OnEachTick;   // need every tick event
                IsOverlay = false;                  // imbalance plot gets its own panel
                DrawOnPricePanel = true;            // arrows/labels go on the price chart

                BigTradeLots = 10;
                BurstTrades = 30;
                BurstWindowSeconds = 10;
                BookDepthLevels = 5;
                MaxDrawings = 400;

                AddPlot(Brushes.DodgerBlue, "Book imbalance %");
                AddLine(Brushes.DimGray, 50, "Balance 50%");
                AddLine(Brushes.Green, 70, "Buy pressure 70%");
                AddLine(Brushes.Red, 30, "Sell pressure 30%");
            }
        }

        protected override void OnBarUpdate()
        {
            // Neutral 50% until real depth data arrives (real-time only)
            if (bidBook.Count == 0 && askBook.Count == 0)
                Values[0][0] = 50;
        }

        // L1: trades and best bid/ask
        protected override void OnMarketData(MarketDataEventArgs e)
        {
            if (CurrentBar < 0)
                return;

            if (e.MarketDataType == MarketDataType.Bid)
            {
                bestBid = e.Price;
                return;
            }
            if (e.MarketDataType == MarketDataType.Ask)
            {
                bestAsk = e.Price;
                return;
            }
            if (e.MarketDataType != MarketDataType.Last)
                return;

            // ---- trade burst detection ------------------------------------
            tradeTimes.RemoveAll(t => (e.Time - t).TotalSeconds > BurstWindowSeconds);
            tradeTimes.Add(e.Time);
            if (tradeTimes.Count >= BurstTrades && lastBurstBar != CurrentBar)
            {
                lastBurstBar = CurrentBar;
                string btag = NextTag();
                Draw.Text(this, btag, "BURST " + tradeTimes.Count, 0, High[0] + 4 * TickSize);
                TrackTag(btag);
            }

            // ---- big trade marker -----------------------------------------
            if (e.Volume >= BigTradeLots)
            {
                bool aggressiveBuy = bestAsk > 0 && e.Price >= bestAsk;
                bool aggressiveSell = bestBid > 0 && e.Price <= bestBid;
                string tag = NextTag();

                if (aggressiveBuy)
                {
                    Draw.ArrowUp(this, tag, false, 0, e.Price - 3 * TickSize, Brushes.LimeGreen);
                    Draw.Text(this, tag + "t", e.Volume.ToString(), 0, e.Price - 6 * TickSize);
                }
                else if (aggressiveSell)
                {
                    Draw.ArrowDown(this, tag, false, 0, e.Price + 3 * TickSize, Brushes.Red);
                    Draw.Text(this, tag + "t", e.Volume.ToString(), 0, e.Price + 6 * TickSize);
                }
                else
                {
                    Draw.Dot(this, tag, false, 0, e.Price, Brushes.Orange);
                    Draw.Text(this, tag + "t", e.Volume.ToString(), 0, e.Price + 6 * TickSize);
                }
                TrackTag(tag);
                TrackTag(tag + "t");
            }
        }

        // L2: order book updates -> price-keyed book (positions are ignored:
        // they are display slots that shuffle, as we learned the hard way)
        protected override void OnMarketDepth(MarketDepthEventArgs e)
        {
            if (e.IsReset)
                return;

            Dictionary<double, long> book =
                (e.MarketDataType == MarketDataType.Bid) ? bidBook : askBook;

            if (e.Operation == Operation.Remove || e.Volume <= 0)
                book.Remove(e.Price);
            else
                book[e.Price] = e.Volume;

            UpdateImbalancePlot();
        }

        private void UpdateImbalancePlot()
        {
            if (CurrentBar < 0)
                return;
            double bidVol = TopVolume(bidBook, true);
            double askVol = TopVolume(askBook, false);
            if (bidVol + askVol <= 0)
                Values[0][0] = 50;
            else
                Values[0][0] = 100.0 * bidVol / (bidVol + askVol);
        }

        private double TopVolume(Dictionary<double, long> book, bool bestFirst)
        {
            IEnumerable<double> keys = bestFirst
                ? book.Keys.OrderByDescending(k => k)
                : book.Keys.OrderBy(k => k);
            int n = 0;
            double vol = 0;
            foreach (double k in keys)
            {
                vol += book[k];
                n++;
                if (n >= BookDepthLevels)
                    break;
            }
            return vol;
        }

        private string NextTag()
        {
            seq++;
            return "GFS" + seq.ToString();
        }

        private void TrackTag(string tag)
        {
            // keep the chart from filling up with drawings forever
            drawTags.Enqueue(tag);
            while (drawTags.Count > MaxDrawings)
                RemoveDrawObject(drawTags.Dequeue());
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Big trade lots", GroupName = "Signals", Order = 1)]
        public int BigTradeLots { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Burst: trades needed", GroupName = "Signals", Order = 2)]
        public int BurstTrades { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Burst: window seconds", GroupName = "Signals", Order = 3)]
        public int BurstWindowSeconds { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Imbalance: depth levels", GroupName = "Signals", Order = 4)]
        public int BookDepthLevels { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Max drawings on chart", GroupName = "Signals", Order = 5)]
        public int MaxDrawings { get; set; }
        #endregion
    }
}
