package dev.toss.app;

import android.content.Intent;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(TossSharePlugin.class);
        super.onCreate(savedInstanceState);
        TossSharePlugin.handle(this, getIntent());
    }

    /** Sharing to toss while it is already open lands here. */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        TossSharePlugin.handle(this, intent);
    }
}
