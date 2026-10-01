package powerhistoryprobe;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1SimpleAttachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.common.Log;
import velox.api.layer1.simplified.Api;
import velox.api.layer1.simplified.BackfilledDataListener;
import velox.api.layer1.simplified.CustomModuleAdapter;
import velox.api.layer1.simplified.HistoricalModeListener;
import velox.api.layer1.simplified.InitialState;
import velox.api.layer1.simplified.TimeListener;
import velox.api.layer1.simplified.TradeDataListener;
import velox.api.layer1.data.InstrumentInfo;
import velox.api.layer1.data.TradeInfo;

/**
 * Standalone, read-only diagnostic add-on. It writes to a NEW probe CSV and
 * never reads or changes the production ticks.csv/mbo.csv bridge files.
 *
 * BackfilledDataListener asks Bookmap to provide available cloud backfill.
 * HistoricalModeListener marks the transition into real-time data.
 */
@Layer1SimpleAttachable
@Layer1StrategyName("POWER History Backfill Probe (standalone)")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION1)
public final class PowerHistoryProbe implements CustomModuleAdapter,
        TradeDataListener, TimeListener, BackfilledDataListener, HistoricalModeListener {

    private static final String HEADER =
            "record_type,phase,event_time_ns,alias,price,size,bid_aggressor_flag";
    private static final DateTimeFormatter FILE_STAMP =
            DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss").withZone(ZoneId.systemDefault());

    private final Object lock = new Object();
    private BufferedWriter writer;
    private Path outputPath;
    private String alias = "";
    private String phase = "PRE_REALTIME";
    private long eventTimeNs = -1L;
    private long historicalTrades;
    private long realtimeTrades;
    private long totalTrades;
    private long firstEventTimeNs = -1L;
    private long lastEventTimeNs = -1L;

    @Override
    public void initialize(String alias, InstrumentInfo info, Api api, InitialState initialState) {
        this.alias = csv(alias == null ? "" : alias);
        try {
            String configuredDir = System.getenv("POWER_HISTORY_PROBE_DIR");
            Path directory = (configuredDir == null || configuredDir.trim().isEmpty())
                    ? Paths.get(System.getProperty("user.home"), "BookmapPowerHistoryProbe")
                    : Paths.get(configuredDir.trim());
            Files.createDirectories(directory);
            String filename = "power_history_probe_" + FILE_STAMP.format(Instant.now()) + ".csv";
            outputPath = directory.resolve(filename);
            writer = Files.newBufferedWriter(outputPath, StandardCharsets.UTF_8);
            writer.write(HEADER);
            writer.newLine();
            writeStatus("PROBE_STARTED", "");
            writer.flush();
            Log.info("POWER History Probe: writing separate diagnostic file: " + outputPath);
            Log.info("POWER History Probe: instrument=" + alias + "; waiting for historical/backfill events");
        } catch (IOException ex) {
            Log.error("POWER History Probe: cannot create diagnostic output: " + ex.getMessage());
            writer = null;
        }
    }

    @Override
    public void onTimestamp(long nanoseconds) {
        eventTimeNs = nanoseconds;
    }

    @Override
    public void onTrade(double price, int size, TradeInfo tradeInfo) {
        synchronized (lock) {
            if (writer == null || eventTimeNs < 0L) {
                return;
            }
            String aggressorFlag = tradeInfo == null
                    ? "UNKNOWN" : Boolean.toString(tradeInfo.isBidAggressor);
            try {
                writer.write("TRADE," + phase + "," + eventTimeNs + "," + alias + ","
                        + String.format(Locale.US, "%.10f", price) + "," + size + "," + aggressorFlag);
                writer.newLine();
                totalTrades++;
                if ("LIVE".equals(phase)) {
                    realtimeTrades++;
                } else {
                    historicalTrades++;
                }
                if (firstEventTimeNs < 0L || eventTimeNs < firstEventTimeNs) {
                    firstEventTimeNs = eventTimeNs;
                }
                if (eventTimeNs > lastEventTimeNs) {
                    lastEventTimeNs = eventTimeNs;
                }
                // Keep the diagnostic durable without flushing every single market event.
                if ((totalTrades & 255L) == 0L) {
                    writer.flush();
                }
            } catch (IOException ex) {
                Log.error("POWER History Probe: CSV write failed: " + ex.getMessage());
                closeWriter();
            }
        }
    }

    @Override
    public void onRealtimeStart() {
        synchronized (lock) {
            phase = "LIVE";
            writeStatus("REALTIME_START", "historical_trades=" + historicalTrades);
            flushWriter();
            Log.info("POWER History Probe: Bookmap entered real-time mode; historical trades="
                    + historicalTrades + "; output=" + outputPath);
        }
    }

    @Override
    public void stop() {
        synchronized (lock) {
            writeStatus("PROBE_STOPPED", "historical_trades=" + historicalTrades
                    + "; realtime_trades=" + realtimeTrades
                    + "; total_trades=" + totalTrades
                    + "; first_event_ns=" + firstEventTimeNs
                    + "; last_event_ns=" + lastEventTimeNs);
            flushWriter();
            closeWriter();
            Log.info("POWER History Probe: stopped; historical trades=" + historicalTrades
                    + "; live trades=" + realtimeTrades + "; output=" + outputPath);
        }
    }

    private void writeStatus(String type, String details) {
        if (writer == null) {
            return;
        }
        try {
            // event_time_ns is blank for wall-clock status markers. Raw market timestamps
            // are retained in TRADE rows exactly as Bookmap supplied them.
            writer.write(type + "," + phase + ",," + alias + ",,," + csv(details));
            writer.newLine();
        } catch (IOException ex) {
            Log.error("POWER History Probe: status write failed: " + ex.getMessage());
        }
    }

    private void flushWriter() {
        if (writer != null) {
            try {
                writer.flush();
            } catch (IOException ex) {
                Log.error("POWER History Probe: flush failed: " + ex.getMessage());
            }
        }
    }

    private void closeWriter() {
        if (writer != null) {
            try {
                writer.close();
            } catch (IOException ignored) {
                // Best-effort close during add-on shutdown.
            }
            writer = null;
        }
    }

    private static String csv(String value) {
        if (value == null) {
            return "";
        }
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }
}
