package powerhistoryprobe;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

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
 * One-shot, bounded Bookmap startup-history collector.
 *
 * It aggregates pre-realtime trades in memory into a rolling set of at most
 * 29 M5 buckets (28 desired complete bars plus one possible unfinished tail).
 * At REALTIME_START it atomically writes at most the latest 28 completed M5
 * OHLCV bars to one fixed CSV, then ignores all later live trades. It never
 * reads or writes the production ticks.csv or mbo.csv bridge files.
 */
@Layer1SimpleAttachable
@Layer1StrategyName("POWER History Backfill Probe (bounded 28 M5 bars)")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION1)
public final class PowerHistoryProbe implements CustomModuleAdapter,
        TradeDataListener, TimeListener, BackfilledDataListener, HistoricalModeListener {

    private static final long BAR_NS = 300L * 1_000_000_000L;
    private static final int REQUIRED_BARS = 28;
    private static final int KEEP_BUCKETS = REQUIRED_BARS + 1;
    private static final String OUTPUT_NAME = "power_history_probe_latest.csv";
    private static final String TEMP_NAME = OUTPUT_NAME + ".tmp";
    private static final String HEADER =
            "record_type,phase,bar_start_ns,bar_end_ns,alias,open,high,low,close,volume,captured_at_utc,status";
    private static final DateTimeFormatter CAPTURE_TIME =
            DateTimeFormatter.ISO_INSTANT.withZone(ZoneOffset.UTC);

    private final Object lock = new Object();
    private final TreeMap<Long, Bar> bars = new TreeMap<Long, Bar>();
    private Path outputDirectory;
    private String alias = "";
    private long eventTimeNs = -1L;
    private long latestHistoricalEventNs = -1L;
    private boolean realtimeStarted;
    private boolean snapshotWritten;

    private static final class Bar {
        final long startNs;
        long firstTradeNs;
        long lastTradeNs;
        double open;
        double high;
        double low;
        double close;
        long volume;

        Bar(long startNs, long timestampNs, double price, int size) {
            this.startNs = startNs;
            this.firstTradeNs = timestampNs;
            this.lastTradeNs = timestampNs;
            this.open = this.high = this.low = this.close = price;
            this.volume = Math.max(0, size);
        }

        void add(long timestampNs, double price, int size) {
            high = Math.max(high, price);
            low = Math.min(low, price);
            if (timestampNs < firstTradeNs) {
                firstTradeNs = timestampNs;
                open = price;
            }
            if (timestampNs >= lastTradeNs) {
                lastTradeNs = timestampNs;
                close = price;
            }
            if (size > 0 && volume <= Long.MAX_VALUE - size) {
                volume += size;
            }
        }
    }

    @Override
    public void initialize(String alias, InstrumentInfo info, Api api, InitialState initialState) {
        this.alias = alias == null ? "" : alias.trim();
        try {
            String configuredDir = System.getenv("POWER_HISTORY_PROBE_DIR");
            outputDirectory = (configuredDir == null || configuredDir.trim().isEmpty())
                    ? Paths.get(System.getProperty("user.home"), "BookmapPowerHistoryProbe")
                    : Paths.get(configuredDir.trim()).toAbsolutePath().normalize();
            Files.createDirectories(outputDirectory);
            Log.info("POWER History Probe bounded v2: instrument=" + this.alias
                    + "; output=" + outputDirectory.resolve(OUTPUT_NAME)
                    + "; collecting startup backfill only");
        } catch (IOException ex) {
            outputDirectory = null;
            Log.error("POWER History Probe bounded v2: cannot prepare output directory: "
                    + ex.getMessage());
        }
    }

    @Override
    public void onTimestamp(long nanoseconds) {
        synchronized (lock) {
            eventTimeNs = nanoseconds;
        }
    }

    @Override
    public void onTrade(double price, int size, TradeInfo tradeInfo) {
        synchronized (lock) {
            if (realtimeStarted || outputDirectory == null || eventTimeNs <= 0L
                    || !Double.isFinite(price) || price <= 0.0) {
                return;
            }
            latestHistoricalEventNs = Math.max(latestHistoricalEventNs, eventTimeNs);
            long bucketStartNs = Math.floorDiv(eventTimeNs, BAR_NS) * BAR_NS;
            Bar bar = bars.get(bucketStartNs);
            if (bar == null) {
                bars.put(bucketStartNs, new Bar(bucketStartNs, eventTimeNs, price, size));
                while (bars.size() > KEEP_BUCKETS) {
                    bars.pollFirstEntry();
                }
            } else {
                bar.add(eventTimeNs, price, size);
            }
        }
    }

    @Override
    public void onRealtimeStart() {
        synchronized (lock) {
            if (snapshotWritten) {
                return;
            }
            realtimeStarted = true;
            List<Bar> completed = latestCompletedBars();
            String status = validate(completed);
            writeSnapshot(completed, status);
            bars.clear();
            snapshotWritten = true;
            Log.info("POWER History Probe bounded v2: snapshot status=" + status
                    + "; completed_m5_bars=" + completed.size() + "/" + REQUIRED_BARS
                    + "; path=" + outputDirectory.resolve(OUTPUT_NAME)
                    + "; live trades will not be recorded");
        }
    }

    @Override
    public void stop() {
        synchronized (lock) {
            if (!snapshotWritten && outputDirectory != null) {
                realtimeStarted = true;
                List<Bar> completed = latestCompletedBars();
                writeSnapshot(completed, "STOPPED_BEFORE_REALTIME");
                bars.clear();
                snapshotWritten = true;
            }
            Log.info("POWER History Probe bounded v2: stopped; no live trade file was maintained");
        }
    }

    private List<Bar> latestCompletedBars() {
        List<Bar> complete = new ArrayList<Bar>();
        if (latestHistoricalEventNs <= 0L) {
            return complete;
        }
        for (Map.Entry<Long, Bar> entry : bars.entrySet()) {
            long start = entry.getKey();
            if (start <= Long.MAX_VALUE - BAR_NS && start + BAR_NS <= latestHistoricalEventNs) {
                complete.add(entry.getValue());
            }
        }
        if (complete.size() > REQUIRED_BARS) {
            return new ArrayList<Bar>(complete.subList(complete.size() - REQUIRED_BARS, complete.size()));
        }
        return complete;
    }

    private static String validate(List<Bar> complete) {
        if (complete.size() < REQUIRED_BARS) {
            return "INSUFFICIENT_BARS";
        }
        for (int i = 1; i < complete.size(); i++) {
            if (complete.get(i).startNs - complete.get(i - 1).startNs != BAR_NS) {
                return "GAP_IN_HISTORY";
            }
        }
        return "READY";
    }

    private void writeSnapshot(List<Bar> complete, String status) {
        if (outputDirectory == null) {
            return;
        }
        Path target = outputDirectory.resolve(OUTPUT_NAME);
        Path temp = outputDirectory.resolve(TEMP_NAME);
        String capturedAt = CAPTURE_TIME.format(Instant.now());
        try {
            try (BufferedWriter writer = Files.newBufferedWriter(temp, StandardCharsets.UTF_8)) {
                writer.write(HEADER);
                writer.newLine();
                for (Bar bar : complete) {
                    writer.write("M5_BAR,PRE_REALTIME," + bar.startNs + ","
                            + (bar.startNs + BAR_NS) + "," + csv(alias) + ","
                            + number(bar.open) + "," + number(bar.high) + ","
                            + number(bar.low) + "," + number(bar.close) + ","
                            + bar.volume + "," + csv(capturedAt) + ",");
                    writer.newLine();
                }
                writer.write("PROBE_COMPLETE,PRE_REALTIME,,,,,,,,,"
                        + csv(capturedAt) + "," + status);
                writer.newLine();
            }
            try {
                Files.move(temp, target, StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (IOException atomicMoveFailure) {
                // Some filesystems do not support atomic replacement of an existing file.
                // Fall back to same-directory replacement; if it also fails, report both.
                try {
                    Files.move(temp, target, StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException fallbackFailure) {
                    fallbackFailure.addSuppressed(atomicMoveFailure);
                    throw fallbackFailure;
                }
            }
        } catch (IOException ex) {
            try {
                Files.deleteIfExists(temp);
            } catch (IOException ignored) {
                // Best effort; never touch unrelated files.
            }
            Log.error("POWER History Probe bounded v2: snapshot write failed: " + ex.getMessage());
        }
    }

    private static String number(double value) {
        return String.format(Locale.US, "%.10f", value);
    }

    private static String csv(String value) {
        if (value == null) {
            return "\"\"";
        }
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }
}
